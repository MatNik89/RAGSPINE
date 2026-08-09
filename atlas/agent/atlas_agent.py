"""atlas-agent: mala skripta u radnikovoj sesiji. Spaja se ODLAZNO prema
serveru (long-poll), prima naredbe i izvršava SAMO iz uske lokalne allowliste.
Sve izvan {run_program(key iz lokalne mape), shutdown, enable_wol, status}
odbija — pa ni kompromitiran server ne pokreće proizvoljan kod na radniku.

Nema eval/shell. run_program pokreće argv iz LOKALNE mape (postavljene pri
instalaciji na tom stroju), NIKAD naredbeni string sa žice — server šalje samo
program_key.
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

# Tvrda LOKALNA allowlista radnji (mora se poklapati s fleet.ACTIONS na serveru)
ACTIONS = ("run_program", "shutdown", "enable_wol", "status")


@dataclass
class AgentConfig:
    server_url: str
    token: str
    program_map: dict = field(default_factory=dict)  # key -> argv lista
    sign_key: str = ""   # HMAC ključ za provjeru potpisa naredbi (prazan = ne provjerava)
    poll_interval_s: float = 5.0


def _device_id_from_token(token: str) -> int:
    try:
        return int((token or "").split(".", 1)[0])
    except (ValueError, IndexError):
        return -1


def _valid_signature(sign_key: str, device_id: int, cmd: dict) -> bool:
    """Provjeri per-uređaj HMAC potpis (veže device_id + id). Prazan sign_key =
    provjera isključena (back-compat). Inače potpis MORA valjati."""
    if not sign_key:
        return True
    canon = json.dumps({"device_id": int(device_id), "id": cmd.get("id"),
                        "action": cmd.get("action"), "program_key": cmd.get("program_key")},
                       sort_keys=True, separators=(",", ":")).encode()
    expected = hmac.new(sign_key.encode(), canon, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, str(cmd.get("sig") or ""))


class _SeqFile:
    """Trajno pamti zadnji prihvaćeni id naredbe (anti-replay). Nedostajuća
    datoteka = svjež agent (0); NEČITLJIVA/prazna = korupcija -> raise (pozivatelj
    fail-closed, ne izvršava). Zapis je ATOMIČAN (temp+fsync+os.replace) i vraća
    bool — ako ne uspije, naredba se NE izvršava."""
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
        return int(data)   # ValueError na smeće -> fail-closed kod pozivatelja

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


def _reject(transport, cmd: dict, detail: str) -> None:
    try:
        transport.report(cmd.get("id"), False, detail)
    except Exception:
        pass


def run_once(cfg: AgentConfig, transport, executor, seq=None) -> dict | None:
    """Jedan poll→izvrši→javi ciklus. Mrežnu/izvršnu grešku guta (petlja se
    sama vrati kasnije), nikad ne ruši agenta. `seq` = anti-replay store."""
    try:
        cmd = transport.poll()
    except Exception:
        return None  # nema veze/naredbe — tiho, backoff u petlji
    if not cmd:
        return None
    if cfg.sign_key:
        did = _device_id_from_token(cfg.token)
        if not _valid_signature(cfg.sign_key, did, cmd):
            _reject(transport, cmd, "odbijeno: nevaljan potpis naredbe")
            return cmd
        # anti-replay: id mora biti STROGO veći od zadnjeg prihvaćenog
        try:
            cid = int(cmd.get("id"))
        except (TypeError, ValueError):
            _reject(transport, cmd, "odbijeno: neispravan id")
            return cmd
        if seq is not None:
            try:
                last = seq.last()
            except (OSError, ValueError):  # korupcija -> fail-closed, ne izvršavaj
                _reject(transport, cmd, "odbijeno: anti-replay zapis nečitljiv")
                return cmd
            if cid <= last:
                _reject(transport, cmd, "odbijeno: replay (stara naredba)")
                return cmd
            if not seq.set(cid):  # trajni zapis mora uspjeti PRIJE izvršenja
                _reject(transport, cmd, "odbijeno: anti-replay zapis nije uspio")
                return cmd
    try:
        ok, detail = _execute(cfg, executor, cmd.get("action"), cmd.get("program_key"))
    except Exception as e:  # izvršenje palo — javi neuspjeh, ne ruši agenta
        ok, detail = False, str(e)
    try:
        transport.report(cmd["id"], ok, detail)
    except Exception:
        pass
    return cmd


def main(cfg: AgentConfig, seq_path: str = "~/.atlas-agent.seq") -> None:  # pragma: no cover — petlja
    import os
    transport, executor = HttpTransport(cfg), LocalExecutor()
    seq = _SeqFile(os.path.expanduser(seq_path)) if cfg.sign_key else None
    while True:
        run_once(cfg, transport, executor, seq=seq)
        time.sleep(cfg.poll_interval_s)
