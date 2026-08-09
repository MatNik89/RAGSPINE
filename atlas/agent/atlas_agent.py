"""atlas-agent: mala skripta u radnikovoj sesiji. Spaja se ODLAZNO prema
serveru (long-poll), prima naredbe i izvršava SAMO iz uske lokalne allowliste.
Sve izvan {run_program(key iz lokalne mape), shutdown, enable_wol, status}
odbija — pa ni kompromitiran server ne pokreće proizvoljan kod na radniku.

Nema eval/shell. run_program pokreće argv iz LOKALNE mape (postavljene pri
instalaciji na tom stroju), NIKAD naredbeni string sa žice — server šalje samo
program_key.
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# Tvrda LOKALNA allowlista radnji (mora se poklapati s fleet.ACTIONS na serveru)
ACTIONS = ("run_program", "shutdown", "enable_wol", "status")


@dataclass
class AgentConfig:
    server_url: str
    token: str
    program_map: dict = field(default_factory=dict)  # key -> argv lista
    poll_interval_s: float = 5.0


# --- izvršenje (pravi executor; injektabilan u testovima) ------------------

class LocalExecutor:
    def run_program(self, argv: list) -> None:
        subprocess.Popen(list(argv))  # argv lista, bez shella

    def shutdown(self) -> None:
        cmd = ["shutdown", "/s", "/t", "0"] if sys.platform.startswith("win") \
            else ["shutdown", "-h", "now"]
        subprocess.run(cmd, check=True)

    def enable_wol(self) -> None:
        # OS-specifično (powercfg / ethtool); ostavljeno kao točka proširenja.
        # Ne diramo mrežnu karticu bez stvarne konfiguracije po stroju.
        raise NotImplementedError("enable_wol nije konfiguriran na ovom stroju")

    def status(self) -> str:
        return f"ok {sys.platform}"


# --- transport (pravi HTTP long-poll; injektabilan) ------------------------

class HttpTransport:
    def __init__(self, cfg: AgentConfig):
        # Device-token je bearer — NIKAD preko cleartext http (procurio bi na
        # LAN-u). Traži https; http bi tražio eksplicitan insecure flag (upgrade).
        if not cfg.server_url.lower().startswith("https://"):
            raise ValueError("server_url mora biti https:// (token ne ide cleartextom)")
        self.cfg = cfg

    def _req(self, path: str, data=None):
        url = self.cfg.server_url.rstrip("/") + path
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body, method="POST" if data is not None else "GET",
                                     headers={"Authorization": self.cfg.token,
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (poznat server)
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


# --- jezgra ----------------------------------------------------------------

def _execute(cfg: AgentConfig, executor, action: str, program_key):
    """Izvrši dopuštenu radnju. Vrati (ok, detail). Odbijanje NE izvršava."""
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


def run_once(cfg: AgentConfig, transport, executor) -> dict | None:
    """Jedan poll→izvrši→javi ciklus. Mrežnu/izvršnu grešku guta (petlja se
    sama vrati kasnije), nikad ne ruši agenta."""
    try:
        cmd = transport.poll()
    except Exception:
        return None  # nema veze/naredbe — tiho, backoff u petlji
    if not cmd:
        return None
    try:
        ok, detail = _execute(cfg, executor, cmd.get("action"), cmd.get("program_key"))
    except Exception as e:  # izvršenje palo — javi neuspjeh, ne ruši agenta
        ok, detail = False, str(e)
    try:
        transport.report(cmd["id"], ok, detail)
    except Exception:
        pass
    return cmd


def main(cfg: AgentConfig) -> None:  # pragma: no cover — petlja
    transport, executor = HttpTransport(cfg), LocalExecutor()
    while True:
        run_once(cfg, transport, executor)
        time.sleep(cfg.poll_interval_s)
