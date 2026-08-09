"""Faza 2 promocija: novi alati (dodaj_vrstu_obveze, nedostajuci_dokumenti,
upit_baze, nauci_izvor, pokreni_program) — svaki tanki omot nad business slojem."""
import pytest

from atlas.business import devices, doc_registry, fleet, obveze, tenancy
from atlas.business.acl import Actor
from atlas.rag import agent, agent_tools


def _actor(spine, role="admin", username="ana"):
    org = tenancy.default_org_id(spine)
    return Actor(user_id=1, org_id=org, role=role, username=username)


def _client(spine, name="Pekara", oib=None):
    with spine.write() as c:
        return c.execute("INSERT INTO clients(name, oib) VALUES(?,?)", (name, oib)).lastrowid


def test_all_new_tools_registered_with_gates():
    for t in ("dodaj_vrstu_obveze", "nauci_izvor", "pokreni_program"):
        assert agent_tools.TOOLS[t].readonly is False and agent_tools.TOOLS[t].min_role in ("member", "admin")
    for t in ("nedostajuci_dokumenti", "upit_baze"):
        assert agent_tools.TOOLS[t].readonly is True


def test_dodaj_vrstu_obveze_writes_type(spine, cfg):
    a = _actor(spine, "member")
    out = agent_tools.run_tool(spine, cfg, a, "dodaj_vrstu_obveze",
                               {"kind": "najam", "label": "Najam", "rule": "monthly:10",
                                "frequency": "monthly", "applies_to": "manual"})
    assert out["kind"] == "NAJAM"
    assert obveze.get_type(spine, "NAJAM")["label"] == "Najam"


def test_dodaj_vrstu_obveze_bad_domain_raises(spine, cfg):
    a = _actor(spine, "member")
    with pytest.raises(ValueError):
        agent_tools.run_tool(spine, cfg, a, "dodaj_vrstu_obveze",
                             {"kind": "X", "frequency": "svako_malo", "applies_to": "all_active"})


def test_nedostajuci_dokumenti(spine, cfg):
    cid = _client(spine)
    doc_registry.upsert(spine, "ugovor", "Ugovor", [])
    doc_registry.upsert(spine, "osobna_iskaznica", "Osobna", [])
    with spine.write() as c:
        c.execute("INSERT INTO client_doc_types(client_id, doc_type_key) VALUES(?,?)", (cid, "ugovor"))
        c.execute("INSERT INTO client_doc_types(client_id, doc_type_key) VALUES(?,?)", (cid, "osobna_iskaznica"))
        c.execute("INSERT INTO documents(title, client_id, doc_type) VALUES('u.pdf',?,?)", (cid, "ugovor"))
    a = _actor(spine, "viewer")
    out = agent_tools.run_tool(spine, cfg, a, "nedostajuci_dokumenti", {"klijent": "Pekara"})
    assert out["nedostaju"] == ["osobna_iskaznica"]
    assert "ugovor" in out["prisutni"]


def test_upit_baze_counts(spine, cfg):
    _client(spine, "A"); _client(spine, "B")
    a = _actor(spine, "viewer")
    out = agent_tools.run_tool(spine, cfg, a, "upit_baze", {"pitanje": "koliko klijenata imamo"})
    assert out["odgovor"] and ("2" in out["odgovor"] or "klijen" in out["odgovor"].lower())


def test_nauci_izvor_member_gate(spine, cfg):
    viewer = _actor(spine, "viewer")
    with pytest.raises(ValueError):  # viewer < member
        agent_tools.run_tool(spine, cfg, viewer, "nauci_izvor", {"url": "https://x.example"})


def test_pokreni_program_admin_only_and_enqueues(spine, cfg):
    fleet.add_program(spine, "preglednik", "Preglednik", user="g")
    devices.add_device(spine, "radna-stanica", "PC-Ana", user="g", host="192.168.1.10",
                       worker_username="ana")
    member = _actor(spine, "member")
    with pytest.raises(ValueError):  # member < admin
        agent_tools.run_tool(spine, cfg, member, "pokreni_program",
                             {"radnik": "ana", "program": "preglednik"})
    admin = _actor(spine, "admin")
    out = agent_tools.run_tool(spine, cfg, admin, "pokreni_program",
                               {"radnik": "ana", "program": "preglednik"})
    assert out["ok"] is True and out["command_id"]


def test_izvezi_excel_asks_when_underspecified(spine, cfg):
    a = _actor(spine, "viewer")
    out = agent_tools.run_tool(spine, cfg, a, "izvezi_excel", {})
    assert out["pitanja"]  # "što izvesti?"
    out2 = agent_tools.run_tool(spine, cfg, a, "izvezi_excel", {"sto": "obveze"})
    assert any("period" in p.lower() or "mjesec" in p.lower() for p in out2["pitanja"])


def test_izvezi_excel_klijenti_builds_downloadable(spine, cfg):
    _client(spine, "Alfa"); _client(spine, "Beta")
    a = _actor(spine, "viewer")
    out = agent_tools.run_tool(spine, cfg, a, "izvezi_excel", {"sto": "klijenti"})
    assert out["redaka"] == 2 and out["preuzmi"].startswith("/export/")
    from atlas.business import excel_export
    token = out["preuzmi"].rsplit("/", 1)[1]
    assert excel_export.path_for(cfg, token)  # datoteka postoji
    assert excel_export.path_for(cfg, "../../etc/passwd") is None  # path traversal blokiran


def test_export_endpoint_auth_and_404(spine, cfg):
    from fastapi.testclient import TestClient
    from atlas.web.api import create_app
    from atlas.web.deps import add_user
    from tests.conftest import complete_setup
    c = TestClient(create_app(spine, cfg))
    assert c.get("/export/whatever").status_code in (401, 403)  # bez logina
    add_user(spine, "ana", "pw"); complete_setup(spine)
    tok = c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert c.get("/export/nepostoji", headers=h).status_code == 404
    _client(spine, "Alfa")
    out = agent_tools.run_tool(spine, cfg, _actor(spine, "viewer"), "izvezi_excel", {"sto": "klijenti"})
    r = c.get(out["preuzmi"], headers=h)
    assert r.status_code == 200 and "spreadsheet" in r.headers["content-type"]


def test_summarize_action_new_write_tools():
    assert "vrstu obveze" in agent.summarize_action("dodaj_vrstu_obveze",
                                                    {"label": "Najam", "frequency": "monthly"}).lower()
    assert "naučit" in agent.summarize_action("nauci_izvor", {"url": "https://x"}).lower()
    assert "pokrenut" in agent.summarize_action("pokreni_program",
                                                {"program": "X", "radnik": "ana"}).lower()
