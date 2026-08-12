"""atlas-agent: a small script running in the worker's session. Connects
OUTBOUND to the server (long-poll), receives commands and executes ONLY from a
narrow local allowlist. Anything outside {run_program(key from local map),
shutdown, enable_wol, status} is rejected -- so even a compromised server cannot
run arbitrary code on the worker.

No eval/shell. run_program launches argv from the LOCAL map (set up at
installation time on that machine), NEVER a command string off the wire -- the
server sends only a program_key.
"""
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# Hard LOCAL allowlist of actions (must match fleet.ACTIONS on the server)
ACTIONS = ("run_program", "shutdown", "enable_wol", "status")


@dataclass
class AgentConfig:
    server_url: str
    token: str
    program_map: dict = field(default_factory=dict)  # key -> argv list
    sign_key: str = ""   # HMAC key for verifying command signatures (empty = no verification)
    poll_interval_s: float = 5.0


def _device_id_from_token(token: str) -> int:
    try:
        return int((token or "").split(".", 1)[0])
    except (ValueError, IndexError):
        return -1


def _valid_signature(sign_key: str, device_id: int, cmd: dict) -> bool:
    """Verify the per-device HMAC signature (binds device_id + id). Empty
    sign_key = verification disabled (back-compat). Otherwise the signature MUST
    be valid."""
    if not sign_key:
        return True
    canon = json.dumps({"device_id": int(device_id), "id": cmd.get("id"),
                        "action": cmd.get("action"), "program_key": cmd.get("program_key")},
                       sort_keys=True, separators=(",", ":")).encode()
    expected = hmac.new(sign_key.encode(), canon, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, str(cmd.get("sig") or ""))


class _SeqFile:
    """Persistently remembers the last accepted command id (anti-replay). A
    missing file = fresh agent (0); UNREADABLE/empty = corruption -> raise (the
    caller fails closed, does not execute). The write is ATOMIC
    (temp+fsync+os.replace) and returns a bool -- if it fails, the command is
    NOT executed."""
    def __init__(self, path: str):
        self.path = path

    def last(self) -> int:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = f.read().strip()
        except FileNotFoundError:
            return 0
        if not data:
            raise ValueError("prazan seq zapis (korupcija)")
        return int(data)   # ValueError on garbage -> fails closed at the caller

    def set(self, seq: int) -> bool:
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(str(seq))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False


# --- execution (real executor; injectable in tests) ------------------------

class LocalExecutor:
    def run_program(self, argv: list) -> None:
        subprocess.Popen(list(argv))  # argv list, no shell

    def shutdown(self) -> None:
        cmd = ["shutdown", "/s", "/t", "0"] if sys.platform.startswith("win") \
            else ["shutdown", "-h", "now"]
        subprocess.run(cmd, check=True)

    def enable_wol(self) -> None:
        # OS-specific (powercfg / ethtool); left as an extension point.
        # We do not touch the network card without real per-machine configuration.
        raise NotImplementedError("enable_wol nije konfiguriran na ovom stroju")

    def status(self) -> str:
        return f"ok {sys.platform}"


# --- transport (real HTTP long-poll; injectable) ---------------------------

class HttpTransport:
    def __init__(self, cfg: AgentConfig):
        # The device token is a bearer -- NEVER over cleartext http (it would
        # leak on the LAN). Require https; http would need an explicit insecure
        # flag (upgrade).
        if not cfg.server_url.lower().startswith("https://"):
            raise ValueError("server_url mora biti https:// (token ne ide cleartextom)")
        self.cfg = cfg

    def _req(self, path: str, data=None):
        url = self.cfg.server_url.rstrip("/") + path
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, method="POST" if data is not None else "GET",
                                     headers={"Authorization": self.cfg.token,
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (known server)
            if resp.status == 204:
                return None
            return json.loads(resp.read() or b"null")

    def poll(self):
        try:
            return self._req("/agent/poll")
        except urllib.error.HTTPError as e:
            if e.code == 204:
                return None
            raise

    def report(self, cmd_id: int, ok: bool, detail: str) -> None:
        self._req("/agent/result", {"id": cmd_id, "ok": ok, "detail": detail})


# --- core ------------------------------------------------------------------

def _execute(cfg: AgentConfig, executor, action: str, program_key):
    """Execute an allowed action. Return (ok, detail). A rejection does NOT execute."""
    if action not in ACTIONS:
        return False, f"odbijeno: nepoznata radnja {action!r}"
    if action == "run_program":
        argv = cfg.program_map.get(program_key)
        if not argv:
            return False, f"odbijeno: program {program_key!r} nije u lokalnoj allowlisti"
        executor.run_program(argv)
        return True, f"pokrenut {program_key}"
    if action == "shutdown":
        executor.shutdown()
        return True, "gašenje pokrenuto"
    if action == "enable_wol":
        executor.enable_wol()
        return True, "WOL uključen"
    detail = executor.status()  # action == "status"
    return True, str(detail)


def _reject(transport, cmd: dict, detail: str) -> None:
    try:
        transport.report(cmd.get("id"), False, detail)
    except Exception:
        pass


def run_once(cfg: AgentConfig, transport, executor, seq=None) -> dict | None:
    """One poll->execute->report cycle. Swallows network/execution errors (the
    loop comes back later on its own), never crashes the agent. `seq` =
    anti-replay store."""
    try:
        cmd = transport.poll()
    except Exception:
        return None  # no connection/command -- quietly, backoff in the loop
    if not cmd:
        return None
    if cfg.sign_key:
        did = _device_id_from_token(cfg.token)
        if not _valid_signature(cfg.sign_key, did, cmd):
            _reject(transport, cmd, "odbijeno: nevaljan potpis naredbe")
            return cmd
        # anti-replay: id must be STRICTLY greater than the last accepted one
        try:
            cid = int(cmd.get("id"))
        except (TypeError, ValueError):
            _reject(transport, cmd, "odbijeno: neispravan id")
            return cmd
        if seq is not None:
            try:
                last = seq.last()
            except (OSError, ValueError):  # corruption -> fail closed, do not execute
                _reject(transport, cmd, "odbijeno: anti-replay zapis nečitljiv")
                return cmd
            if cid <= last:
                _reject(transport, cmd, "odbijeno: replay (stara naredba)")
                return cmd
            if not seq.set(cid):  # the persistent write must succeed BEFORE execution
                _reject(transport, cmd, "odbijeno: anti-replay zapis nije uspio")
                return cmd
    try:
        ok, detail = _execute(cfg, executor, cmd.get("action"), cmd.get("program_key"))
    except Exception as e:  # execution failed -- report failure, do not crash the agent
        ok, detail = False, str(e)
    try:
        transport.report(cmd["id"], ok, detail)
    except Exception:
        pass
    return cmd


def main(cfg: AgentConfig, seq_path: str = "~/.atlas-agent.seq") -> None:  # pragma: no cover -- loop
    import os
    transport, executor = HttpTransport(cfg), LocalExecutor()
    seq = _SeqFile(os.path.expanduser(seq_path)) if cfg.sign_key else None
    while True:
        run_once(cfg, transport, executor, seq=seq)
        time.sleep(cfg.poll_interval_s)
