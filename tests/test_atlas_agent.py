"""Faza 5 T3: atlas-agent klijent — tvrda LOKALNA allowlista, injektiran
transport/executor. Agent odbija sve što nije u lokalnoj mapi, bez obzira što
server pošalje (obrana od kompromitiranog servera)."""
from atlas.agent import atlas_agent as aa


class FakeTransport:
    def __init__(self, command):
        self._command = command
        self.reported = []

    def poll(self):
        return self._command

    def report(self, cmd_id, ok, detail):
        self.reported.append({"id": cmd_id, "ok": ok, "detail": detail})


class FakeExecutor:
    def __init__(self):
        self.ran = []
        self.calls = []

    def run_program(self, argv):
        self.ran.append(argv)

    def shutdown(self):
        self.calls.append("shutdown")

    def enable_wol(self):
        self.calls.append("enable_wol")

    def status(self):
        self.calls.append("status")
        return "ziv"


def _cfg(program_map=None):
    return aa.AgentConfig(server_url="https://server.lan:8443", token="1.tajna",
                          program_map=program_map or {"preglednik": ["firefox", "--kiosk"]})


def test_run_program_known_key_executes_argv():
    tr = FakeTransport({"id": 5, "action": "run_program", "program_key": "preglednik"})
    ex = FakeExecutor()
    aa.run_once(_cfg(), tr, ex)
    assert ex.ran == [["firefox", "--kiosk"]]
    assert tr.reported[0]["id"] == 5 and tr.reported[0]["ok"] is True


def test_unknown_program_key_refused_not_executed():
    tr = FakeTransport({"id": 6, "action": "run_program", "program_key": "nema_u_mapi"})
    ex = FakeExecutor()
    aa.run_once(_cfg(), tr, ex)
    assert ex.ran == []  # NIJE izvršeno
    assert tr.reported[0]["ok"] is False and "odbijeno" in tr.reported[0]["detail"].lower()


def test_unknown_action_refused():
    tr = FakeTransport({"id": 7, "action": "format_disk", "program_key": None})
    ex = FakeExecutor()
    aa.run_once(_cfg(), tr, ex)
    assert ex.calls == [] and ex.ran == []
    assert tr.reported[0]["ok"] is False


def test_shutdown_and_wol_and_status_dispatch():
    for action, expect in [("shutdown", "shutdown"), ("enable_wol", "enable_wol"),
                           ("status", "status")]:
        tr = FakeTransport({"id": 1, "action": action, "program_key": None})
        ex = FakeExecutor()
        aa.run_once(_cfg(), tr, ex)
        assert expect in ex.calls
        assert tr.reported[0]["ok"] is True


def test_no_command_does_nothing():
    tr = FakeTransport(None)  # 204 -> nema naredbe
    ex = FakeExecutor()
    assert aa.run_once(_cfg(), tr, ex) is None
    assert tr.reported == []


def test_executor_failure_reported_not_raised():
    class Boom(FakeExecutor):
        def shutdown(self):
            raise OSError("nije uspjelo")
    tr = FakeTransport({"id": 9, "action": "shutdown", "program_key": None})
    aa.run_once(_cfg(), tr, Boom())
    assert tr.reported[0]["ok"] is False and "nije uspjelo" in tr.reported[0]["detail"]


def test_http_transport_requires_https():
    import pytest
    with pytest.raises(ValueError):
        aa.HttpTransport(aa.AgentConfig(server_url="http://server.lan:8443", token="1.t"))
    aa.HttpTransport(aa.AgentConfig(server_url="https://server.lan:8443", token="1.t"))  # ok


def test_transport_poll_error_does_not_crash():
    class BadPoll:
        def poll(self):
            raise OSError("mreža pukla")
        def report(self, *a):
            raise AssertionError("ne smije reportati bez naredbe")
    # run_once guta mrežnu grešku (petlja se sama vrati kasnije)
    assert aa.run_once(_cfg(), BadPoll(), FakeExecutor()) is None
