"""Faza 4 T2: stroj stanja UPS + gašenje redom (armed-gate, debounce, audit).

Sve injektirano — reader (UPS status), runner (izvršenje gašenja), notifier —
nula stvarnog SSH-a/shutdowna. Vrijeme `now` je epoch sekunde (float).
"""
from atlas.business import devices, power


def _reader(**status):
    status.setdefault("ok", True)
    status.setdefault("on_battery", False)
    status.setdefault("low", False)
    status.setdefault("charge", 100)
    status.setdefault("runtime_s", None)
    return lambda host, port, ups: dict(status)


class _Runner:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, step):
        self.calls.append(step["name"])
        if step["name"] == self.fail_on:
            raise OSError("ssh pao")


class _Notifier:
    def __init__(self):
        self.events = []

    def __call__(self, kind, body):
        self.events.append(body)


def _mk_devices(spine):
    # namjerno dodani izvan reda gašenja da test dokaže sortiranje
    devices.add_device(spine, "radna-stanica", "NAS", user="a", host="192.168.1.20",
                       caps={"shutdown_order": 3})
    devices.add_device(spine, "radna-stanica", "PC-Ana", user="a", host="192.168.1.10",
                       caps={"shutdown_order": 1})
    devices.add_device(spine, "radna-stanica", "PC-Bez-Hosta", user="a",
                       caps={"shutdown_order": 2})  # nema host -> preskočen
    devices.add_device(spine, "radna-stanica", "PC-Nadzor", user="a", host="192.168.1.30",
                       caps={"shutdown_order": None})  # nije za gašenje


def test_shutdown_plan_ordered_server_last_hostless_skipped(spine):
    _mk_devices(spine)
    plan = power.shutdown_plan(spine)
    names = [s["name"] for s in plan["steps"]]
    assert names == ["PC-Ana", "NAS", "server"]  # redom 1,3, pa server zadnji
    assert plan["steps"][-1]["method"] == "local"
    assert plan["steps"][0]["method"] == "ssh"
    assert "PC-Bez-Hosta" in plan["skipped"]  # bez hosta = ne može se ugasiti (faza 5)
    assert "PC-Nadzor" not in names  # shutdown_order None se ne gasi


def test_ssh_cmd_is_arglist_no_shell():
    cmd = power.ssh_shutdown_cmd({"host": "192.168.1.10", "worker_username": "ana"})
    assert cmd[0] == "ssh"
    assert "ana@192.168.1.10" in cmd
    assert "BatchMode=yes" in " ".join(cmd)
    # host/user su zasebni argv elementi, nikad spojeni u shell string
    assert not any(";" in part or "&&" in part for part in cmd)


def test_ssh_cmd_rejects_option_injection():
    # worker_username koji ssh tumači kao opciju (-oProxyCommand=...) = RCE; odbij
    import pytest
    with pytest.raises(ValueError):
        power.ssh_shutdown_cmd({"host": "192.168.1.10",
                                "worker_username": "-oProxyCommand=touch /tmp/pwn"})
    with pytest.raises(ValueError):
        power.ssh_shutdown_cmd({"host": "-oProxyCommand=evil", "worker_username": "ana"})


def test_evaluate_skips_injection_device_still_shuts_server(spine, cfg):
    # zloćudan uređaj se preskoči (audit fail), server se svejedno ugasi zadnji
    devices.add_device(spine, "radna-stanica", "ZLI", user="a", host="192.168.1.10",
                       worker_username="-oProxyCommand=touch /tmp/pwn",
                       caps={"shutdown_order": 1})
    power.save_config(spine, nut_host="192.168.1.5", on_battery_seconds=1, armed=True)

    executed = []
    def real_ish_runner(step):
        # simulira _default_runner: ssh korak gradi cmd (baci na zli), local radi
        if step["method"] == "ssh":
            power.ssh_shutdown_cmd(step)  # baci ValueError na zli cilj
        executed.append(step["name"])

    rdr = _reader(on_battery=True)
    power.evaluate(spine, cfg, now=1000.0, reader=rdr, runner=real_ish_runner, notifier=_Notifier())
    out = power.evaluate(spine, cfg, now=1002.0, reader=rdr, runner=real_ish_runner,
                         notifier=_Notifier())
    assert out["executed"] == ["server"]  # zli preskočen, server ugašen
    assert spine.read().execute(
        "SELECT 1 FROM audit_log WHERE action='power_shutdown_fail'").fetchone() is not None


def test_on_battery_below_threshold_no_shutdown(spine, cfg):
    _mk_devices(spine)
    power.save_config(spine, nut_host="192.168.1.5", on_battery_seconds=120, armed=True)
    runner, notifier = _Runner(), _Notifier()
    out = power.evaluate(spine, cfg, now=1000.0, reader=_reader(on_battery=True),
                         runner=runner, notifier=notifier)
    assert out["status"] == "OB" and out["shutdown"] is False
    assert runner.calls == []
    assert any("bateriji" in e for e in notifier.events)


def test_on_battery_over_threshold_shuts_down_in_order(spine, cfg):
    _mk_devices(spine)
    power.save_config(spine, nut_host="192.168.1.5", on_battery_seconds=120, armed=True)
    runner, notifier = _Runner(), _Notifier()
    rdr = _reader(on_battery=True)
    power.evaluate(spine, cfg, now=1000.0, reader=rdr, runner=runner, notifier=notifier)
    assert runner.calls == []  # tek ušao na bateriju
    out = power.evaluate(spine, cfg, now=1000.0 + 121, reader=rdr, runner=runner, notifier=notifier)
    assert out["shutdown"] is True
    assert runner.calls == ["PC-Ana", "NAS", "server"]  # redom, server zadnji
    row = spine.read().execute(
        "SELECT COUNT(*) n FROM audit_log WHERE action='power_shutdown'").fetchone()
    assert row["n"] == 3


def test_low_battery_shuts_down_immediately(spine, cfg):
    _mk_devices(spine)
    power.save_config(spine, nut_host="192.168.1.5", on_battery_seconds=99999, armed=True)
    runner = _Runner()
    out = power.evaluate(spine, cfg, now=1000.0, reader=_reader(on_battery=True, low=True),
                         runner=runner, notifier=_Notifier())
    assert out["shutdown"] is True  # LB gasi bez čekanja praga
    assert runner.calls[-1] == "server"


def test_disarmed_never_shuts_down(spine, cfg):
    _mk_devices(spine)
    power.save_config(spine, nut_host="192.168.1.5", on_battery_seconds=1, armed=False)
    runner, notifier = _Runner(), _Notifier()
    power.evaluate(spine, cfg, now=1000.0, reader=_reader(on_battery=True, low=True),
                   runner=runner, notifier=notifier)
    out = power.evaluate(spine, cfg, now=2000.0, reader=_reader(on_battery=True, low=True),
                         runner=runner, notifier=notifier)
    assert out["shutdown"] is False and runner.calls == []
    assert any("baterij" in e.lower() for e in notifier.events)  # ali alarmira


def test_shutdown_is_idempotent_not_repeated(spine, cfg):
    _mk_devices(spine)
    power.save_config(spine, nut_host="192.168.1.5", on_battery_seconds=1, armed=True)
    runner = _Runner()
    rdr = _reader(on_battery=True)
    power.evaluate(spine, cfg, now=1000.0, reader=rdr, runner=runner, notifier=_Notifier())
    power.evaluate(spine, cfg, now=1005.0, reader=rdr, runner=runner, notifier=_Notifier())
    first = list(runner.calls)
    power.evaluate(spine, cfg, now=1010.0, reader=rdr, runner=runner, notifier=_Notifier())
    assert runner.calls == first  # nije ponovno gasio


def test_power_restored_resets_and_notifies(spine, cfg):
    _mk_devices(spine)
    power.save_config(spine, nut_host="192.168.1.5", on_battery_seconds=1, armed=True)
    notifier = _Notifier()
    power.evaluate(spine, cfg, now=1000.0, reader=_reader(on_battery=True), notifier=notifier,
                   runner=_Runner())
    out = power.evaluate(spine, cfg, now=1002.0, reader=_reader(), notifier=notifier,
                         runner=_Runner())
    assert out["status"] == "OL"
    assert any("vratila" in e for e in notifier.events)


def test_ups_unreachable_does_not_shutdown(spine, cfg):
    _mk_devices(spine)
    power.save_config(spine, nut_host="192.168.1.5", on_battery_seconds=1, armed=True)
    runner, notifier = _Runner(), _Notifier()
    out = power.evaluate(spine, cfg, now=1000.0,
                         reader=lambda h, p, u: {"ok": False, "error": "refused"},
                         runner=runner, notifier=notifier)
    assert out["status"] == "unknown" and out["shutdown"] is False
    assert runner.calls == []
    assert any("UPS" in e for e in notifier.events)


def test_ups_unreachable_notifies_once_not_every_tick(spine, cfg):
    # 30s poller ne smije spamati istu obavijest dok je UPS nedostupan
    power.save_config(spine, nut_host="192.168.1.5", armed=True)
    notifier = _Notifier()
    down = lambda h, p, u: {"ok": False, "error": "refused"}
    for t in (1000.0, 1030.0, 1060.0):
        power.evaluate(spine, cfg, now=t, reader=down, runner=_Runner(), notifier=notifier)
    assert len([e for e in notifier.events if "UPS" in e]) == 1  # samo prijelaz
