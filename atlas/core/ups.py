# NUT (Network UPS Tools) klijent nad stdlib socketom — bez ovisnosti.
# Protokol upsd (port 3493) je linijski tekst: `GET VAR <ups> <var>` -> `VAR
# <ups> <var> "<vrijednost>"` ili `ERR <kod>`. Čita se SAMO (nikad SET), pa je
# read-only prema UPS-u.
#
# Fail-closed: svaka greška (host nije LAN, spoj pukne, ERR, malformed) vraća
# {"ok": False, ...} — NIKAD se tiho ne tumači kao "struja je OK". Pozivatelj
# (business/power.evaluate) na ok=False NE gasi ništa, samo alarmira.
import math
import socket

from atlas.core import lan

_DEFAULT_PORT = 3493


def _default_connect(ip: str, port: int, timeout: float):
    return socket.create_connection((ip, port), timeout=timeout)


def _readline(sock) -> str | None:
    """Pročitaj jednu \\n-terminiranu liniju s NUT socketa (bounded). Vrati None
    ako veza padne bez potpune linije ili je linija oversize — oboje je
    malformed i NE smije se tumačiti kao valjan odgovor (fail-closed)."""
    buf = bytearray()
    while b"\n" not in buf:
        chunk = sock.recv(256)
        if not chunk:
            return None  # veza zatvorena bez terminirane linije
        buf.extend(chunk)
        if len(buf) > 4096:  # NUT linije su kratke; oversize = smeće
            return None
    return buf.split(b"\n", 1)[0].decode("utf-8", "replace").strip()


def _get_var(sock, ups: str, var: str) -> str | None:
    """Vrati vrijednost varijable ili None (ERR/nepodržano/malformed)."""
    sock.sendall(f"GET VAR {ups} {var}\n".encode())
    line = _readline(sock)
    if not line or not line.startswith("VAR "):
        return None  # ERR ..., oversize, nepotpuno ili neočekivano
    q = line.find('"')
    if q == -1:
        return None
    return line[q + 1: line.rfind('"')]


def _get_int(sock, ups: str, var: str) -> int | None:
    val = _get_var(sock, ups, var)
    if val is None:
        return None
    try:
        f = float(val)  # battery.runtime zna biti "1800.0"
    except ValueError:
        return None
    if not math.isfinite(f):  # "inf"/"nan" -> odbaci (int(inf) baca OverflowError)
        return None
    try:
        return int(f)
    except (ValueError, OverflowError):
        return None


def read_status(host: str, port: int = _DEFAULT_PORT, ups: str = "ups",
                timeout: float = 3.0, connect=None) -> dict:
    try:
        ip = lan.assert_lan_host(host, port)  # spoj se na VRAĆENI ip (anti-rebind)
    except Exception as e:  # LanBlocked i sve ostalo -> fail-closed
        return {"ok": False, "error": f"host nije LAN: {e}"}
    connect = connect or _default_connect
    try:
        sock = connect(ip, port, timeout)
    except OSError as e:
        return {"ok": False, "error": f"connect: {e}"}
    try:
        status = _get_var(sock, ups, "ups.status")
        if status is None:
            return {"ok": False, "error": "ups.status nedostupan"}
        flags = set(status.split())
        charge = _get_int(sock, ups, "battery.charge")
        runtime = _get_int(sock, ups, "battery.runtime")
    except OSError as e:
        return {"ok": False, "error": f"io: {e}"}
    finally:
        try:
            sock.sendall(b"LOGOUT\n")
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass
    return {"ok": True, "flags": flags,
            "on_battery": "OB" in flags, "low": "LB" in flags,
            "charge": charge, "runtime_s": runtime}
