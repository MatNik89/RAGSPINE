# NUT (Network UPS Tools) client over a stdlib socket — no dependencies.
# The upsd protocol (port 3493) is line text: `GET VAR <ups> <var>` -> `VAR
# <ups> <var> "<value>"` or `ERR <code>`. Only READ (never SET), so it is
# read-only toward the UPS.
#
# Fail-closed: any error (host not LAN, connection breaks, ERR, malformed) returns
# {"ok": False, ...} — NEVER silently interpreted as "power is OK". The caller
# (business/power.evaluate) shuts nothing down on ok=False, only raises an alarm.
import math
import socket

from atlas.core import lan

_DEFAULT_PORT = 3493


def _default_connect(ip: str, port: int, timeout: float):
    return socket.create_connection((ip, port), timeout=timeout)


def _readline(sock) -> str | None:
    """Read one \\n-terminated line from the NUT socket (bounded). Return None
    if the connection drops without a complete line or the line is oversize — both
    are malformed and MUST NOT be interpreted as a valid response (fail-closed)."""
    buf = bytearray()
    while b"\n" not in buf:
        chunk = sock.recv(256)
        if not chunk:
            return None  # connection closed without a terminated line
        buf.extend(chunk)
        if len(buf) > 4096:  # NUT lines are short; oversize = garbage
            return None
    return buf.split(b"\n", 1)[0].decode("utf-8", "replace").strip()


def _get_var(sock, ups: str, var: str) -> str | None:
    """Return the variable value or None (ERR/unsupported/malformed)."""
    sock.sendall(f"GET VAR {ups} {var}\n".encode())
    line = _readline(sock)
    if not line or not line.startswith("VAR "):
        return None  # ERR ..., oversize, incomplete or unexpected
    q = line.find('"')
    if q == -1:
        return None
    return line[q + 1: line.rfind('"')]


def _get_int(sock, ups: str, var: str) -> int | None:
    val = _get_var(sock, ups, var)
    if val is None:
        return None
    try:
        f = float(val)  # battery.runtime can be "1800.0"
    except ValueError:
        return None
    if not math.isfinite(f):  # "inf"/"nan" -> reject (int(inf) raises OverflowError)
        return None
    try:
        return int(f)
    except (ValueError, OverflowError):
        return None


def read_status(host: str, port: int = _DEFAULT_PORT, ups: str = "ups",
                timeout: float = 3.0, connect=None) -> dict:
    try:
        ip = lan.assert_lan_host(host, port)  # connect to the RETURNED ip (anti-rebind)
    except Exception as e:  # LanBlocked and everything else -> fail-closed
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
