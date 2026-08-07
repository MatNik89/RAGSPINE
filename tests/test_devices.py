"""Piece E: LAN-only sloj, mDNS parse, eSCL sken, IPP print, registar uređaja."""
import http.server
import struct
import threading

import pytest

from atlas.business import devices
from atlas.core import lan, optional

fitz = optional.need("fitz", "test PDF")


# --- lan_fetch guard ----------------------------------------------------------

def test_lan_fetch_blocks_public_address():
    with pytest.raises(lan.LanBlocked):
        lan.assert_lan_host("example.com", 80)  # javna adresa
    with pytest.raises(lan.LanBlocked):
        lan.lan_fetch("http://8.8.8.8/")
    with pytest.raises(lan.LanBlocked):
        lan.lan_fetch("ftp://127.0.0.1/")


def test_lan_fetch_allows_loopback():
    lan.assert_lan_host("127.0.0.1", 80)  # ne baca


# --- mDNS parse (fixture paket, bez mreže) ------------------------------------

def _label(s):
    return b"".join(bytes([len(x)]) + x.encode() for x in s.split(".")) + b"\x00"


def _mdns_response():
    """PTR + SRV + TXT + A za jedan eSCL skener (ručno složen paket)."""
    head = struct.pack(">HHHHHH", 0, 0x8400, 0, 4, 0, 0)
    svc = _label("_uscan._tcp.local")
    inst = _label("Ured Skener._uscan._tcp.local")
    host = _label("skener.local")
    ptr = svc + struct.pack(">HHIH", 12, 1, 120, len(inst)) + inst
    srv_rd = struct.pack(">HHH", 0, 0, 8080) + host
    srv = inst + struct.pack(">HHIH", 33, 1, 120, len(srv_rd)) + srv_rd
    txt_rd = bytes([len(b"rs=eSCL")]) + b"rs=eSCL"
    txt = inst + struct.pack(">HHIH", 16, 1, 120, len(txt_rd)) + txt_rd
    a = host + struct.pack(">HHIH", 1, 1, 120, 4) + bytes([192, 168, 1, 50])
    return head + ptr + srv + txt + a


def test_mdns_parse_and_assemble():
    recs = lan._parse_records(_mdns_response())
    devs = lan._assemble(recs)
    assert devs == [{"kind": "scanner", "name": "Ured Skener",
                     "url": "http://192.168.1.50:8080/eSCL"}]


def test_mdns_assemble_rejects_public_ip():
    pkt = _mdns_response().replace(bytes([192, 168, 1, 50]), bytes([8, 8, 8, 8]))
    assert lan._assemble(lan._parse_records(pkt)) == []


def test_mdns_parse_garbage_safe():
    assert lan._parse_records(b"\x00\x01") == []
    assert lan._parse_records(b"") == []


# --- fake eSCL skener + IPP pisač na loopbacku --------------------------------

def _tiny_pdf(text):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


class _DeviceHandler(http.server.BaseHTTPRequestHandler):
    pages: list[bytes] = []
    served: int = 0
    ipp_requests: list[bytes] = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if self.path.endswith("/ScanJobs"):
            type(self).served = 0
            self.send_response(201)
            self.send_header("Location", "/eSCL/ScanJobs/job1")
            self.end_headers()
        elif self.path.endswith("/ipp/print"):
            type(self).ipp_requests.append(body)
            resp = b"\x01\x01" + b"\x00\x00" + body[4:8] + b"\x03"
            self.send_response(200)
            self.send_header("Content-Type", "application/ipp")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
        else:
            self.send_response(404); self.end_headers()

    def do_GET(self):
        if self.path.endswith("/NextDocument"):
            if type(self).served < len(type(self).pages):
                data = type(self).pages[type(self).served]
                type(self).served += 1
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404); self.end_headers()
        else:
            self.send_response(404); self.end_headers()

    def do_DELETE(self):
        self.send_response(200); self.end_headers()


@pytest.fixture
def device_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _DeviceHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.mark.skipif(fitz is None, reason="fitz not installed")
def test_escl_scan_collects_documents(device_server):
    _DeviceHandler.pages = [_tiny_pdf("str 1"), _tiny_pdf("str 2")]
    docs = lan.escl_scan(f"{device_server}/eSCL")
    assert len(docs) == 2 and all("pdf" in ct for _b, ct in docs)


def test_ipp_print_roundtrip(device_server):
    _DeviceHandler.ipp_requests = []
    lan.ipp_print(f"{device_server}/ipp/print", b"%PDF-fake")
    req = _DeviceHandler.ipp_requests[0]
    assert req[:2] == b"\x01\x01" and req[2:4] == b"\x00\x02"
    assert b"printer-uri" in req and b"application/pdf" in req
    assert req.endswith(b"%PDF-fake")


# --- registar + tijek ---------------------------------------------------------

def test_registry_validates_and_crud(spine):
    with pytest.raises(ValueError):
        devices.add_device(spine, "toster", "X", "http://127.0.0.1/")
    with pytest.raises(ValueError):
        devices.add_device(spine, "scanner", "", "http://127.0.0.1/")
    with pytest.raises(lan.LanBlocked):
        devices.add_device(spine, "scanner", "Zli", "http://8.8.8.8/eSCL")
    d = devices.add_device(spine, "scanner", "Ured", "http://127.0.0.1:9999/eSCL")
    assert [x["name"] for x in devices.list_devices(spine)] == ["Ured"]
    assert devices.list_devices(spine, kind="printer") == []
    devices.remove_device(spine, d["id"])
    assert devices.list_devices(spine) == []


@pytest.mark.skipif(fitz is None, reason="fitz not installed")
def test_scan_saves_single_pdf_in_scans_fallback(spine, cfg, device_server):
    _DeviceHandler.pages = [_tiny_pdf("prva"), _tiny_pdf("druga")]
    d = devices.add_device(spine, "scanner", "Ured", f"{device_server}/eSCL")
    res = devices.scan(spine, cfg, d["id"], user="ana")
    assert res["pages"] == 2
    import os
    assert os.path.dirname(res["path"]).endswith("scans")
    doc = fitz.open(res["path"])
    try:
        assert doc.page_count == 2
    finally:
        doc.close()
    n = spine.read().execute(
        "SELECT COUNT(*) AS n FROM notifications WHERE kind='scan_done'").fetchone()["n"]
    assert n == 1


@pytest.mark.skipif(fitz is None, reason="fitz not installed")
def test_print_doc_sends_document(spine, cfg, tmp_path, device_server):
    _DeviceHandler.ipp_requests = []
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(_tiny_pdf("Račun 55"))
    with spine.write() as c:
        doc_id = c.execute("INSERT INTO documents(title, path) VALUES('Račun 55', ?)",
                           (str(pdf),)).lastrowid
    d = devices.add_device(spine, "printer", "Pisač", f"{device_server}/ipp/print")
    res = devices.print_doc(spine, cfg, d["id"], doc_id, user="ana")
    assert res["submitted"] is True
    assert _DeviceHandler.ipp_requests and _DeviceHandler.ipp_requests[0].endswith(
        pdf.read_bytes())


def test_scan_wrong_kind_rejected(spine, cfg):
    d = devices.add_device(spine, "printer", "P", "http://127.0.0.1:9999/ipp/print")
    with pytest.raises(ValueError):
        devices.scan(spine, cfg, d["id"])
    with pytest.raises(ValueError):
        devices.print_doc(spine, cfg, 424242, 1)


# --- API ----------------------------------------------------------------------

def test_api_devices_admin_gate_and_scan(spine, cfg, device_server):
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    from atlas.web.deps import add_user
    from tests.conftest import complete_setup

    c = TestClient(create_app(spine, cfg))
    assert c.get("/devices").status_code in (401, 403)  # prije logina
    add_user(spine, "gazda", "pw")
    complete_setup(spine)
    owner = c.post("/auth/login", json={"username": "gazda", "password": "pw"}).json()["token"]
    add_user(spine, "boris", "pw")
    worker = c.post("/auth/login", json={"username": "boris", "password": "pw"}).json()["token"]
    ho = {"Authorization": f"Bearer {owner}"}
    hw = {"Authorization": f"Bearer {worker}"}

    # registar mijenja samo admin
    payload = {"kind": "scanner", "name": "Ured", "url": f"{device_server}/eSCL"}
    assert c.post("/devices", headers=hw, json=payload).status_code == 403
    assert c.get("/devices/discover", headers=hw).status_code == 403
    r = c.post("/devices", headers=ho, json=payload)
    assert r.status_code == 200
    dev_id = r.json()["id"]
    # javna adresa odbijena
    assert c.post("/devices", headers=ho, json={
        "kind": "printer", "name": "Zli", "url": "http://8.8.8.8/ipp"}).status_code == 400

    # radnik VIDI uređaje (odabir pri akciji) i smije skenirati
    assert [d["name"] for d in c.get("/devices", headers=hw).json()] == ["Ured"]
    if fitz is not None:
        _DeviceHandler.pages = [_tiny_pdf("api sken")]
        res = c.post(f"/devices/{dev_id}/scan", headers=hw)
        assert res.status_code == 200 and res.json()["pages"] == 1

    assert c.delete(f"/devices/{dev_id}", headers=hw).status_code == 403
    assert c.delete(f"/devices/{dev_id}", headers=ho).status_code == 200
    r = c.get("/ui/uredjaji", headers=ho)
    assert r.status_code == 200 and "Uređaji" in r.text


def test_escl_partial_scan_rejected(device_server, monkeypatch):
    # deadline istekne prije terminal 404 -> djelomičan rezultat se ODBACUJE
    _DeviceHandler.pages = [b"%PDF-x"] * 3
    real = lan.lan_fetch
    def slow(url, **kw):
        if url.endswith("/NextDocument") and _DeviceHandler.served >= 1:
            import time
            time.sleep(0.3)
            return 503, {}, b""
        return real(url, **kw)
    monkeypatch.setattr(lan, "lan_fetch", slow)
    with pytest.raises(RuntimeError, match="nije dovršen"):
        lan.escl_scan(f"{device_server}/eSCL", timeout=1)


def test_escl_cross_origin_location_rejected(device_server, monkeypatch):
    real = lan.lan_fetch
    def evil(url, **kw):
        st, h, b = real(url, **kw)
        if url.endswith("/ScanJobs"):
            h = dict(h); h["location"] = "http://192.168.1.99/eSCL/ScanJobs/job1"
        return st, h, b
    monkeypatch.setattr(lan, "lan_fetch", evil)
    with pytest.raises(RuntimeError, match="drugi origin"):
        lan.escl_scan(f"{device_server}/eSCL")


def test_print_guarded_by_client_visibility(spine, cfg, tmp_path, device_server):
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    from atlas.web.deps import add_user

    c = TestClient(create_app(spine, cfg))
    add_user(spine, "gazda", "pw")
    owner = c.post("/auth/login", json={"username": "gazda", "password": "pw"}).json()["token"]
    add_user(spine, "boris", "pw")
    worker = c.post("/auth/login", json={"username": "boris", "password": "pw"}).json()["token"]
    ho = {"Authorization": f"Bearer {owner}"}
    hw = {"Authorization": f"Bearer {worker}"}

    with spine.write() as conn:
        cid = conn.execute("INSERT INTO clients(name) VALUES('Tajni')").lastrowid
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(b"%PDF-tajno")
    with spine.write() as conn:
        doc_id = conn.execute(
            "INSERT INTO documents(title, path, client_id) VALUES('T', ?, ?)",
            (str(pdf), cid)).lastrowid
    bid = spine.read().execute("SELECT id FROM users WHERE username='boris'").fetchone()["id"]
    c.post(f"/workers/{bid}/visibility", json={"sees_all": False, "client_ids": []}, headers=ho)

    d = c.post("/devices", headers=ho, json={
        "kind": "printer", "name": "P", "url": f"{device_server}/ipp/print"}).json()
    r = c.post(f"/devices/{d['id']}/print", headers=hw, json={"doc_id": doc_id})
    assert r.status_code == 403
