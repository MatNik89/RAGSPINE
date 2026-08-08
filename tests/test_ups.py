"""Faza 4 T1: NUT klijent (stdlib socket, fail-closed, LAN-only) + config napajanja."""
import pytest

from atlas.business import power
from atlas.core import ups


class FakeNUT:
    """Skriptirani NUT server: mapira ime varijable -> vrijednost (ili None=ERR)."""

    def __init__(self, variables: dict, fail_send=False):
        self.variables = variables
        self.sent = []
        self._buf = b""
        self.closed = False
        self.fail_send = fail_send

    def sendall(self, data: bytes):
        if self.fail_send:
            raise OSError("pukla veza")
        cmd = data.decode().strip()
        self.sent.append(cmd)
        parts = cmd.split()
        if parts[:2] == ["GET", "VAR"]:
            upsname, var = parts[2], parts[3]
            val = self.variables.get(var)
            if val is None:
                self._buf += b"ERR VAR-NOT-SUPPORTED\n"
            else:
                self._buf += f'VAR {upsname} {var} "{val}"\n'.encode()
        else:
            self._buf += b"OK\n"

    def recv(self, n: int) -> bytes:
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def close(self):
        self.closed = True


def _connect_ok(variables, **kw):
    fake = FakeNUT(variables, **kw)
    return lambda ip, port, timeout: fake, fake


def test_read_status_online():
    conn, fake = _connect_ok({"ups.status": "OL", "battery.charge": "100",
                              "battery.runtime": "1800"})
    r = ups.read_status("192.168.1.5", ups="ups", connect=conn)
    assert r["ok"] is True
    assert r["on_battery"] is False and r["low"] is False
    assert r["charge"] == 100 and r["runtime_s"] == 1800
    assert fake.closed is True


def test_read_status_on_battery_low():
    conn, _ = _connect_ok({"ups.status": "OB DISCHRG LB", "battery.charge": "12"})
    r = ups.read_status("192.168.1.5", connect=conn)
    assert r["ok"] is True
    assert r["on_battery"] is True and r["low"] is True
    assert r["charge"] == 12 and r["runtime_s"] is None  # runtime nije podržan -> None


def test_read_status_var_error_is_fail_closed():
    conn, _ = _connect_ok({})  # ups.status -> ERR
    r = ups.read_status("192.168.1.5", connect=conn)
    assert r["ok"] is False and "ups.status" in r["error"]


def test_read_status_socket_failure_fail_closed():
    def bad_connect(ip, port, timeout):
        raise OSError("connection refused")
    r = ups.read_status("192.168.1.5", connect=bad_connect)
    assert r["ok"] is False and "connect" in r["error"]


def test_read_status_send_failure_fail_closed():
    conn, _ = _connect_ok({"ups.status": "OL"}, fail_send=True)
    r = ups.read_status("192.168.1.5", connect=conn)
    assert r["ok"] is False


def test_read_status_non_finite_number_does_not_crash():
    # Codex T1 MEDIUM: "inf"/"nan" ne smiju rušiti (OverflowError) — fail-closed
    conn, _ = _connect_ok({"ups.status": "OL", "battery.charge": "inf",
                           "battery.runtime": "nan"})
    r = ups.read_status("192.168.1.5", connect=conn)
    assert r["ok"] is True
    assert r["charge"] is None and r["runtime_s"] is None


class _OversizeSock:
    """NUT socket koji na ups.status vrati liniju bez \\n (oversize/nepotpuno)."""
    def __init__(self):
        self._buf = b"VAR ups ups.status \"OL" + b"X" * 5000  # nikad ne zatvori liniju
    def sendall(self, data): pass
    def recv(self, n):
        out, self._buf = self._buf[:n], self._buf[n:]
        return out
    def close(self): pass


def test_read_status_oversize_line_is_fail_closed():
    r = ups.read_status("192.168.1.5", connect=lambda *a: _OversizeSock())
    assert r["ok"] is False


def test_read_status_non_lan_host_rejected():
    # javni host se ne smije ni pokušati spojiti (anti-SSRF); connect ne bi smio biti zvan
    called = []
    r = ups.read_status("8.8.8.8", connect=lambda *a: called.append(1))
    assert r["ok"] is False
    assert called == []  # odbijeno prije spajanja


# --- config napajanja ------------------------------------------------------

def test_config_defaults_safe(spine):
    cfg = power.get_config(spine)
    assert cfg["enabled"] is False
    assert cfg["armed"] is False  # default: samo alarm, nikad auto-gašenje
    assert cfg["nut_port"] == 3493
    assert cfg["on_battery_seconds"] > 0


def test_config_roundtrip(spine):
    power.save_config(spine, enabled=True, nut_host="192.168.1.5", ups_name="apc",
                      on_battery_seconds=120, armed=True)
    cfg = power.get_config(spine)
    assert cfg["enabled"] is True and cfg["nut_host"] == "192.168.1.5"
    assert cfg["ups_name"] == "apc" and cfg["on_battery_seconds"] == 120
    assert cfg["armed"] is True


def test_get_config_reclamps_bad_stored_values(spine):
    # Codex T1: read boundary mora održati invariant — ručno pokvareni redovi
    # (legacy/manual edit) NE smiju izaći; fail-safe na default za destruktivnu putanju
    spine.set_override("napajanje", "nut_port", "70000")
    spine.set_override("napajanje", "on_battery_seconds", "-5")
    cfg = power.get_config(spine)
    assert cfg["nut_port"] == 3493  # default, ne 70000
    assert cfg["on_battery_seconds"] == 120  # default, ne -5


def test_config_rejects_public_host_and_bad_values(spine):
    with pytest.raises(ValueError):
        power.save_config(spine, nut_host="8.8.8.8")
    with pytest.raises(ValueError):
        power.save_config(spine, nut_port=70000)
    with pytest.raises(ValueError):
        power.save_config(spine, on_battery_seconds=-5)
