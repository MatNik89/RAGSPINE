"""Phase 4: power (UPS/NUT) — config + state machine + orderly shutdown.

This module holds the power config (T1) and, in T2, a state machine that on a
sustained power outage shuts down devices by `caps.shutdown_order` from phase 2.
Shutdown is DESTRUCTIVE: default `armed=False` (alarm only), executed only once
the user explicitly arms it.
"""
import sys

from atlas.business import devices
from atlas.core import lan
from atlas.core import subproc
from atlas.core.ups import read_status

_MODULE = "napajanje"
_STATE = "napajanje_state"  # runtime machine state (separate from config)

_DEFAULTS = {
    "enabled": False,
    "nut_host": "",
    "nut_port": 3493,
    "ups_name": "ups",
    "on_battery_seconds": 120,
    "armed": False,  # never auto-shutdown until the user turns it on
}
_BOOL_FIELDS = ("enabled", "armed")
_INT_FIELDS = ("nut_port", "on_battery_seconds")


def get_config(spine) -> dict:
    out = dict(_DEFAULTS)
    for key, default in _DEFAULTS.items():
        raw = spine.get_override(_MODULE, key, None)
        if raw is None:
            continue
        if key in _BOOL_FIELDS:
            out[key] = raw in ("1", "true", "True", True)
        elif key in _INT_FIELDS:
            # the read boundary holds the same invariant as save (fail-safe for
            # the destructive path): a corrupted row -> default, do not leak through
            try:
                val = int(raw)
            except (ValueError, TypeError):
                continue
            if key == "nut_port" and not (1 <= val <= 65535):
                continue
            if key == "on_battery_seconds" and val <= 0:
                continue
            out[key] = val
        else:
            out[key] = raw
    return out


def save_config(spine, **fields) -> dict:
    unknown = set(fields) - set(_DEFAULTS)
    if unknown:
        raise ValueError(f"nepoznata polja: {sorted(unknown)}")

    if "nut_host" in fields:
        host = (fields["nut_host"] or "").strip()
        if host:
            try:
                lan.assert_lan_host(host, 0)  # LAN-only UPS/NUT server (anti-SSRF)
            except Exception as e:
                raise ValueError(f"NUT host mora biti na LAN-u: {e}") from e
        fields["nut_host"] = host
    if "nut_port" in fields:
        port = int(fields["nut_port"])
        if not (1 <= port <= 65535):
            raise ValueError("port mora biti 1..65535")
        fields["nut_port"] = port
    if "on_battery_seconds" in fields:
        secs = int(fields["on_battery_seconds"])
        if secs <= 0:
            raise ValueError("prag 'na bateriji' mora biti > 0 sekundi")
        fields["on_battery_seconds"] = secs
    if "ups_name" in fields:
        name = (fields["ups_name"] or "").strip()
        if not name:
            raise ValueError("naziv UPS-a je obavezan")
        fields["ups_name"] = name

    for key, val in fields.items():
        stored = "1" if (key in _BOOL_FIELDS and val) else "0" if key in _BOOL_FIELDS else str(val)
        spine.set_override(_MODULE, key, stored)
    return get_config(spine)


# --- T2: state machine + orderly shutdown ----------------------------------

def _local_shutdown_cmd() -> list[str]:
    if sys.platform.startswith("win"):
        return ["shutdown", "/s", "/t", "0"]
    return ["shutdown", "-h", "now"]


def _ssh_safe(token: str) -> bool:
    # ssh ITSELF interprets an argv that starts with '-' as an OPTION (e.g.
    # -oProxyCommand=...), so the arg-list does NOT protect against injection via
    # user/host — a leading '-' = RCE on the server. Allow only a tame set of
    # characters for hostname/username.
    return bool(token) and not token.startswith("-") and all(
        ch.isalnum() or ch in ".-_:" for ch in token)


def ssh_shutdown_cmd(device: dict) -> list[str]:
    """Argv to shut down a remote device via the system ssh. NEVER a shell.
    Rejects a user/host that ssh would interpret as an option (leading '-') or
    that contains unexpected characters (@, space, '=') — otherwise ProxyCommand
    injection = RCE."""
    user = (device.get("worker_username") or "root").strip()
    host = (device.get("host") or "").strip()
    if not _ssh_safe(user) or not _ssh_safe(host):
        raise ValueError(f"nesiguran ssh cilj: user={user!r} host={host!r}")
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
            f"{user}@{host}", "shutdown", "-h", "now"]


def shutdown_plan(spine) -> dict:
    """Devices with caps.shutdown_order (non-None) in ascending order (workers
    first), the server as a synthetic last step. A device without a host cannot
    be shut down without an agent (phase 5) -> goes to 'skipped'."""
    steps, skipped = [], []
    cand = [d for d in devices.list_devices(spine)
            if d.get("caps", {}).get("shutdown_order") is not None]
    cand.sort(key=lambda d: d["caps"]["shutdown_order"])
    for d in cand:
        if not d.get("host"):
            skipped.append(d["name"])
            continue
        steps.append({"name": d["name"], "host": d["host"],
                      "worker_username": d.get("worker_username"),
                      "order": d["caps"]["shutdown_order"], "method": "ssh"})
    steps.append({"name": "server", "method": "local"})  # server always last
    return {"steps": steps, "skipped": skipped}


def _default_runner(step: dict) -> None:
    cmd = _local_shutdown_cmd() if step["method"] == "local" else ssh_shutdown_cmd(step)
    subproc.run_isolated(cmd, timeout=30)


def _default_notifier(spine):
    def notify(kind: str, body: str) -> None:
        with spine.write() as c:
            c.execute("INSERT INTO notifications(kind, body) VALUES(?,?)", (kind, body))
    return notify


def _cur_state(status: dict) -> str:
    return "LB" if status["low"] else "OB" if status["on_battery"] else "OL"


def evaluate(spine, cfg, now, reader=read_status, runner=None, notifier=None) -> dict:
    """One tick of the state machine. Injectable (reader/runner/notifier/now)
    for tests. `now` = epoch seconds. Shuts down ONLY when armed AND (sustained
    OB > threshold OR LB), once (idempotent until power returns). ok=False does
    NOT shut down."""
    pc = get_config(spine)
    notifier = notifier or _default_notifier(spine)
    runner = runner or _default_runner

    prev = spine.get_override(_STATE, "last_state", "OL")

    st = reader(pc["nut_host"], pc["nut_port"], pc["ups_name"])
    if not st.get("ok"):
        # do NOT shut down on an unknown state; alarm ONLY on a transition
        # (otherwise every 30s tick would spam the same notification while the
        # UPS is unreachable)
        if prev != "unknown":
            notifier("napajanje", f"UPS nedostupan: {st.get('error', '?')}")
        spine.set_override(_STATE, "last_state", "unknown")
        return {"status": "unknown", "shutdown": False, "executed": []}

    cur = _cur_state(st)
    since_raw = spine.get_override(_STATE, "on_battery_since", "") or None

    if cur != prev:
        if cur == "LB" and prev != "LB":
            notifier("napajanje", "Niska baterija UPS-a — gasim redom ako je naoružano.")
        elif cur == "OB" and prev not in ("OB", "LB"):
            notifier("napajanje", "Nestanak struje — UPS radi na bateriji.")
        elif cur == "OL" and prev in ("OB", "LB"):
            notifier("napajanje", "Struja se vratila — napajanje uredno.")

    if cur == "OL":
        since = None
    elif prev in ("OL", "unknown") or since_raw is None:
        since = float(now)  # just went onto battery (or out of the unknown state)
    else:
        since = float(since_raw)

    do_shutdown = False
    if pc["armed"] and cur in ("OB", "LB"):
        if cur == "LB":
            do_shutdown = True
        elif since is not None and (float(now) - since) >= pc["on_battery_seconds"]:
            do_shutdown = True

    already = spine.get_override(_STATE, "shutdown_done", "0") == "1"
    executed = []
    if do_shutdown and not already:
        plan = shutdown_plan(spine)
        for step in plan["steps"]:
            try:
                runner(step)
                spine.audit("napajanje", "power_shutdown", step["name"], step["method"])
                executed.append(step["name"])
            except Exception as e:  # one failure must not stop the rest of the sequence
                spine.audit("napajanje", "power_shutdown_fail", step["name"], str(e))
        spine.set_override(_STATE, "shutdown_done", "1")

    spine.set_override(_STATE, "last_state", cur)
    spine.set_override(_STATE, "on_battery_since", "" if since is None else str(since))
    if cur == "OL":
        spine.set_override(_STATE, "shutdown_done", "0")  # reset for the next outage

    return {"status": cur, "shutdown": bool(executed), "executed": executed}
