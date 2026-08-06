# Setup Wizard P3 — Stranica 4 (Mreža + HTTPS + servis) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stranica 4 wizarda: izbor bind IP-a i porta (s provjerom zauzetosti), self-signed SAN certifikat + HTTPS serviranje, Windows servis/firewall/ACL (preskočivo), winget auto-install (odgođen iz P1), proxy polje; `mark_complete` se pomiče iza stagea 4.

**Architecture:** Mrežne postavke žive u `config_overrides(module='net', ...)` — `_cmd_serve` ih primijeni na cfg prije `uvicorn.run` (isti obrazac kao `model_settings.apply` za model). Certifikat generira novi `ops/certs.py` kroz **postojeću** dependenciju `cryptography` (x509, EC ključ, SAN s IP+hostname). Windows-specifično (winget, sc.exe, netsh advfirewall, icacls) ide kroz postojeći `run_isolated`, Windows-only guard, na drugim OS-ima ispiši naredbu/systemd unit; testovi sve mockaju. Novi CLI `ragspine trust` ispisuje putanju certa + SHA256 fingerprint + upute za klijente.

**Tech Stack:** Python 3.11+ stdlib (socket, ipaddress, ssl) + postojeće dependencije (`cryptography`, `uvicorn`). Bez novih dependencija.

**Svjesna odstupanja od speca (dokumentirano, ne blokira):**
- DPAPI LocalMachine za `secret.key` → ODGOĐENO (backlog): P3 daje file-based secret (postojeći `data_dir/secret`, 0600) + Windows `icacls` ACL + export/restore upute na stranici. `ponytail:` marker u kodu.
- AD CS/GPO preferencija → samo napomena u ispisu (implementacija = backlog).
- Servisni račun: `sc.exe` servis se kreira pod `LocalService` (ugrađeni low-priv račun) umjesto custom `ragspine_svc` — bez upravljanja lozinkom/gMSA problematike u P3; custom račun = backlog. Napomena u ispisu.

## Global Constraints

- Jezik koda/komentara/UI stringova: hrvatski (latinica, S dijakriticima — č ć š ž đ). Cyrillic-gate `tests/test_no_cyrillic.py` zelen.
- Python floor: 3.11+. Bez novih dependencija (`cryptography` i `uvicorn` već postoje).
- CI zelen na 4 posla. Testovi bez mreže/stdina/pravih subprocessa — mockaj `run_isolated`, socket operacije po potrebi; cert-testovi smiju generirati pravi cert u tmp_path (čisti CPU, bez mreže).
- Mrežne postavke u `config_overrides(module='net', key ∈ {host, port, cert_path, key_path})` preko `spine.set_override/get_override` — NE nova tablica.
- Windows-only izvršavanje: SAMO kroz `run_isolated` s listom argumenata (nikad shell string); na ne-Windows platformama funkcije vraćaju upute/False bez izvršavanja.
- Winget allowlist HARDKODIRANA: `{"tesseract": "UB-Mannheim.TesseractOCR", "ollama": "Ollama.Ollama"}` — ništa dinamičko.
- `mark_complete` TOČNO JEDNOM u `run()`, iza stagea 4 (pomiče se s trenutne pozicije iza stagea 3). Skip-grane vraćaju True.
- Svi prompti injektabilni `input_fn`/`out`.

---

### Task 1: Net overrides + helperi (local_ip, port_free) + primjena u _cmd_serve

**Files:**
- Modify: `ragspine/ops/preflight.py` (helperi uz postojeće mrežne funkcije)
- Modify: `ragspine/__main__.py` (`_cmd_serve`, oko retka 22)
- Test: `tests/test_preflight.py`, `tests/test_cli.py` (dodaj)

**Interfaces:**
- Produces:
  - `preflight.local_ip() -> str` — primarni LAN IP UDP-connect trikom (bez slanja paketa); `"127.0.0.1"` na grešku.
  - `preflight.port_free(host: str, port: int) -> bool` — bind-test.
  - `net_overrides(spine, cfg) -> tuple[str, int, str, str]` u `ragspine/__main__.py` (privatni helper `_net_overrides`) — vrati (host, port, cert_path, key_path) iz `config_overrides(module='net')` s cfg fallbackom.
  - `_cmd_serve` koristi te vrijednosti; kad cert+key postoje i datoteke postoje → `uvicorn.run(..., ssl_certfile=, ssl_keyfile=)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preflight.py  (dodaj)
def test_local_ip_returns_string(monkeypatch):
    class _S:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def connect(self, addr): pass
        def getsockname(self): return ("192.168.1.7", 12345)
    monkeypatch.setattr(pf.socket, "socket", lambda *a, **k: _S())
    assert pf.local_ip() == "192.168.1.7"


def test_local_ip_fallback_on_error(monkeypatch):
    def _boom(*a, **k):
        raise OSError("nema mreže")
    monkeypatch.setattr(pf.socket, "socket", _boom)
    assert pf.local_ip() == "127.0.0.1"


def test_port_free_true_and_false():
    import socket as s
    srv = s.socket(s.AF_INET, s.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    taken = srv.getsockname()[1]
    try:
        assert pf.port_free("127.0.0.1", taken) is False
        srv2 = s.socket(s.AF_INET, s.SOCK_STREAM)
        srv2.bind(("127.0.0.1", 0))
        free = srv2.getsockname()[1]
        srv2.close()
        assert pf.port_free("127.0.0.1", free) is True
    finally:
        srv.close()
```

```python
# tests/test_cli.py  (dodaj; pogledaj postojeći stil konstrukcije spine/cfg u tom fajlu)
def test_net_overrides_apply(tmp_path):
    from ragspine.core.spine import init_spine
    from ragspine.__main__ import _net_overrides
    from ragspine.config import Config
    s = init_spine(str(tmp_path / "t.db"))
    cfg = Config(data_dir=str(tmp_path))          # prilagodi postojećem obrascu u fajlu
    host, port, cert, key = _net_overrides(s, cfg)
    assert (host, port) == (cfg.host, cfg.port)   # bez overridea -> cfg
    s.set_override("net", "host", "0.0.0.0")
    s.set_override("net", "port", "8443")
    s.set_override("net", "cert_path", "/x/cert.pem")
    s.set_override("net", "key_path", "/x/key.pem")
    host, port, cert, key = _net_overrides(s, cfg)
    assert (host, port, cert, key) == ("0.0.0.0", 8443, "/x/cert.pem", "/x/key.pem")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_preflight.py -k "local_ip or port_free" -v && pytest tests/test_cli.py -k net_overrides -v`
Expected: FAIL — funkcije ne postoje

- [ ] **Step 3: Write minimal implementation**

```python
# ragspine/ops/preflight.py  (socket je vec importan module-level? ako ne, dodaj `import socket`)

def local_ip() -> str:
    """Primarni LAN IP — UDP connect trik (ne šalje pakete). 127.0.0.1 na grešku."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 53))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def port_free(host: str, port: int) -> bool:
    """True kad se port može bindati (slobodan)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return True
    except OSError:
        return False
```

```python
# ragspine/__main__.py  — helper + primjena u _cmd_serve

def _net_overrides(spine, cfg):
    """Mrežne postavke iz config_overrides(module='net') s cfg fallbackom."""
    host = spine.get_override("net", "host") or cfg.host
    try:
        port = int(spine.get_override("net", "port") or cfg.port)
    except ValueError:
        port = cfg.port
    cert = spine.get_override("net", "cert_path") or ""
    key = spine.get_override("net", "key_path") or ""
    return host, port, cert, key
```

U `_cmd_serve` zamijeni `uvicorn.run(create_app(spine, cfg), host=cfg.host, port=cfg.port, server_header=False)`:

```python
    host, port, cert, key = _net_overrides(spine, cfg)
    ssl_kw = {}
    if cert and key and Path(cert).exists() and Path(key).exists():
        ssl_kw = {"ssl_certfile": cert, "ssl_keyfile": key}
    uvicorn.run(create_app(spine, cfg), host=host, port=port,
                server_header=False, **ssl_kw)
```

(`Path` — provjeri je li importan u __main__.py; dodaj ako nije.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_preflight.py tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ragspine/ops/preflight.py ragspine/__main__.py tests/test_preflight.py tests/test_cli.py
git commit -m "feat(net): net overrides (host/port/cert) + local_ip/port_free helperi"
```

---

### Task 2: ops/certs.py — self-signed SAN cert + `ragspine trust`

**Files:**
- Create: `ragspine/ops/certs.py`
- Modify: `ragspine/__main__.py` (novi subcommand `trust`)
- Test: `tests/test_certs.py` (novi)

**Interfaces:**
- Produces:
  - `certs.generate_self_signed(out_dir: str, ips: list[str], hostnames: list[str] | None = None, days: int = 3650) -> tuple[str, str]` — generira EC (SECP256R1) ključ + self-signed cert sa SAN (IP + DNS unosi); zapiše `out_dir/cert.pem` i `out_dir/key.pem` (key 0600); vrati putanje. Postojeće datoteke NE gazi — vrati postojeće.
  - `certs.fingerprint_sha256(cert_path: str) -> str` — heks SHA256 fingerprint certa (formatiran `AA:BB:...`).
  - CLI `ragspine trust` — ispiše putanju certa (iz net overrides), fingerprint i hrvatske upute za instalaciju NA KLIJENTIMA (samo javni cert; privatni ključ se NIKAD ne distribuira).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_certs.py  (novi)
import ssl

from ragspine.ops import certs


def test_generate_and_fingerprint(tmp_path):
    cert, key = certs.generate_self_signed(str(tmp_path), ips=["192.168.1.7"],
                                           hostnames=["ragspine.local"])
    assert cert.endswith("cert.pem") and key.endswith("key.pem")
    # cert je parsabilan standardnim ssl modulom
    der = ssl.PEM_cert_to_DER_cert(open(cert).read())
    assert len(der) > 100
    fp = certs.fingerprint_sha256(cert)
    assert len(fp.replace(":", "")) == 64 and fp.count(":") == 31


def test_generate_idempotent(tmp_path):
    c1, k1 = certs.generate_self_signed(str(tmp_path), ips=["10.0.0.1"])
    fp1 = certs.fingerprint_sha256(c1)
    c2, k2 = certs.generate_self_signed(str(tmp_path), ips=["10.0.0.2"])
    assert (c1, k1) == (c2, k2)
    assert certs.fingerprint_sha256(c2) == fp1   # ne regenerira postojeći


def test_key_file_private(tmp_path):
    import os, stat
    _, key = certs.generate_self_signed(str(tmp_path), ips=["10.0.0.1"])
    mode = stat.S_IMODE(os.stat(key).st_mode)
    assert mode == 0o600
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_certs.py -v`
Expected: FAIL — `ModuleNotFoundError: ragspine.ops.certs`

- [ ] **Step 3: Write minimal implementation**

```python
# ragspine/ops/certs.py
"""Self-signed SAN certifikat za LAN HTTPS. Koristi postojeću `cryptography`
dependenciju. Javni cert (cert.pem) smije se dijeliti klijentima; key.pem NIKAD.
Napomena: ako ured ima AD CS/GPO, preferiraj domenski cert (backlog)."""
import ipaddress
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def generate_self_signed(out_dir: str, ips: list[str],
                         hostnames: list[str] | None = None,
                         days: int = 3650) -> tuple[str, str]:
    """Generiraj (ili vrati postojeći) cert.pem + key.pem sa SAN unosima."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cert_p, key_p = out / "cert.pem", out / "key.pem"
    if cert_p.exists() and key_p.exists():
        return str(cert_p), str(key_p)

    key = ec.generate_private_key(ec.SECP256R1())
    cn = (hostnames or ips or ["ragspine"])[0]
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    san_entries: list[x509.GeneralName] = [
        x509.DNSName(h) for h in (hostnames or [])
    ] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_p.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    os.chmod(key_p, 0o600)
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(cert_p), str(key_p)


def fingerprint_sha256(cert_path: str) -> str:
    """SHA256 fingerprint certa, formatiran AA:BB:..."""
    cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    raw = cert.fingerprint(hashes.SHA256()).hex().upper()
    return ":".join(raw[i:i + 2] for i in range(0, len(raw), 2))
```

CLI u `ragspine/__main__.py` — novi handler + registracija u `_build_parser` (pogledaj postojeći obrazac za npr. `doctor`):

```python
def _cmd_trust(args) -> int:
    from ragspine.config import get_config
    from ragspine.core.spine import init_spine
    from ragspine.ops import certs
    cfg = get_config()
    spine = init_spine(cfg.db_path)
    cert = spine.get_override("net", "cert_path") or ""
    if not cert or not Path(cert).exists():
        print("Nema certifikata — pokreni `ragspine setup` (stranica Mreža) da ga generiraš.")
        return 1
    print(f"Javni certifikat: {cert}")
    print(f"SHA256 fingerprint: {certs.fingerprint_sha256(cert)}")
    print("")
    print("Instalacija na klijentima (SAMO cert.pem — key.pem se nikad ne dijeli):")
    print("  Windows: dupli klik na cert.pem → Instaliraj → 'Pouzdana izdavačka tijela'")
    print("           (ili GPO distribucija ako postoji AD domena).")
    print("  Provjeri fingerprint prije instalacije — mora se podudarati s gornjim.")
    return 0
```

Test za CLI (dodaj u tests/test_certs.py):

```python
def test_trust_command_prints_fingerprint(tmp_path, monkeypatch, capsys):
    from ragspine import __main__ as m
    from ragspine.core.spine import init_spine
    from ragspine.config import Config, set_config
    cfg = Config(data_dir=str(tmp_path))          # prilagodi stvarnom obrascu
    set_config(cfg)
    try:
        s = init_spine(cfg.db_path)
        cert, _ = certs.generate_self_signed(str(tmp_path), ips=["10.0.0.1"])
        s.set_override("net", "cert_path", cert)
        rc = m.main(["trust"])
        out = capsys.readouterr().out
        assert rc == 0 and "SHA256" in out and "key.pem se nikad" in out
    finally:
        set_config(None)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_certs.py -v && python -m pytest tests/test_no_cyrillic.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ragspine/ops/certs.py ragspine/__main__.py tests/test_certs.py
git commit -m "feat(net): self-signed SAN cert (ops/certs) + ragspine trust naredba"
```

---

### Task 3: Winget auto-install + pip llmfit + proxy polje

**Files:**
- Modify: `ragspine/ops/preflight.py` (instalacijski helperi uz postojeće fix stringove)
- Test: `tests/test_preflight.py` (dodaj)

**Interfaces:**
- Produces:
  - `WINGET_IDS = {"tesseract": "UB-Mannheim.TesseractOCR", "ollama": "Ollama.Ollama"}` (hardkodirana allowlista)
  - `install_via_winget(key: str, *, out=print) -> bool` — SAMO Windows: `run_isolated(["winget", "install", "--exact", "--id", WINGET_IDS[key], "--source", "winget", "--accept-package-agreements", "--accept-source-agreements"], timeout=600)`; ispiše UAC napomenu prije; nakon installa validira (`shutil.which` za binarnu komandu: tesseract→"tesseract", ollama→"ollama"); PATH-problem poruka („restartaj terminal / dodaj PATH ručno"). Ne-Windows: ispiše naredbu, vrati False. Nepoznat key → ValueError.
  - `install_llmfit(*, out=print) -> bool` — `run_isolated([sys.executable, "-m", "pip", "install", "--user", "llmfit"], timeout=600)` + validacija `shutil.which("llmfit")` ili import; sve platforme. (Fallback za slučaj da je ragspine instaliran bez depsa.)
  - `set_proxy(spine, proxy: str) -> None` / `get_proxy(spine) -> str` — `config_overrides(module='setup', key='proxy')`; prazan string briše.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_preflight.py  (dodaj)
def test_install_via_winget_windows_path(monkeypatch):
    calls = []
    monkeypatch.setattr(pf.platform, "system", lambda: "Windows")
    monkeypatch.setattr(pf, "run_isolated",
                        lambda cmd, timeout=60, **kw: calls.append(cmd) or (0, "", ""))
    monkeypatch.setattr(pf.shutil, "which", lambda name: f"C:/x/{name}.exe")
    lines = []
    assert pf.install_via_winget("ollama", out=lines.append) is True
    assert calls and calls[0][:4] == ["winget", "install", "--exact", "--id"]
    assert "Ollama.Ollama" in calls[0]
    assert any("UAC" in l for l in lines)


def test_install_via_winget_non_windows_prints_cmd(monkeypatch):
    monkeypatch.setattr(pf.platform, "system", lambda: "Linux")
    called = []
    monkeypatch.setattr(pf, "run_isolated",
                        lambda *a, **k: called.append(1) or (0, "", ""))
    lines = []
    assert pf.install_via_winget("tesseract", out=lines.append) is False
    assert not called                       # ništa se ne izvršava
    assert any("winget install" in l for l in lines)


def test_install_via_winget_unknown_key():
    import pytest
    with pytest.raises(ValueError):
        pf.install_via_winget("nepoznato")


def test_install_via_winget_path_problem(monkeypatch):
    monkeypatch.setattr(pf.platform, "system", lambda: "Windows")
    monkeypatch.setattr(pf, "run_isolated", lambda cmd, timeout=60, **kw: (0, "", ""))
    monkeypatch.setattr(pf.shutil, "which", lambda name: None)   # instaliran ali ne na PATH-u
    lines = []
    assert pf.install_via_winget("ollama", out=lines.append) is False
    assert any("PATH" in l for l in lines)


def test_proxy_roundtrip(tmp_path):
    from ragspine.core.spine import init_spine
    s = init_spine(str(tmp_path / "t.db"))
    assert pf.get_proxy(s) == ""
    pf.set_proxy(s, "http://proxy.ured.local:3128")
    assert pf.get_proxy(s) == "http://proxy.ured.local:3128"
    pf.set_proxy(s, "")
    assert pf.get_proxy(s) == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_preflight.py -k "install_via_winget or proxy_roundtrip" -v`
Expected: FAIL — funkcije ne postoje

- [ ] **Step 3: Write minimal implementation**

```python
# ragspine/ops/preflight.py  (platform i shutil su vec module-level — provjeri)

WINGET_IDS = {"tesseract": "UB-Mannheim.TesseractOCR", "ollama": "Ollama.Ollama"}
_WINGET_BIN = {"tesseract": "tesseract", "ollama": "ollama"}


def install_via_winget(key: str, *, out=print) -> bool:
    """Windows auto-install preko winget allowliste (UAC potvrda iskoči korisniku).
    Drugi OS: ispiši naredbu i vrati False. Validira binary nakon installa."""
    if key not in WINGET_IDS:
        raise ValueError(f"nepoznat paket: {key!r}")
    wid = WINGET_IDS[key]
    cmd = ["winget", "install", "--exact", "--id", wid, "--source", "winget",
           "--accept-package-agreements", "--accept-source-agreements"]
    if platform.system() != "Windows":
        out(f"Auto-install je Windows-only. Ručno: {' '.join(cmd)}")
        out("  (Linux: apt/dnf; macOS: brew — potraži paket u svom package manageru.)")
        return False
    out(f"Instaliram {wid} — očekuj UAC potvrdu (klikni Da)...")
    rc, _out_txt, err = run_isolated(cmd, timeout=600)
    if rc != 0:
        out(f"winget nije uspio (rc {rc}): {err[:200]}")
        return False
    if not shutil.which(_WINGET_BIN[key]):
        out(f"Instalirano, ali '{_WINGET_BIN[key]}' nije na PATH-u — "
            "restartaj terminal ili dodaj PATH ručno pa ponovi provjeru.")
        return False
    out(f"✓ {wid} instaliran i dostupan.")
    return True


def install_llmfit(*, out=print) -> bool:
    """pip install llmfit (fallback kad ragspine nije instaliran s depsima)."""
    out("Instaliram llmfit (pip)...")
    rc, _o, err = run_isolated([sys.executable, "-m", "pip", "install", "--user", "llmfit"],
                               timeout=600)
    if rc != 0:
        out(f"pip nije uspio: {err[:200]}")
        return False
    ok = bool(shutil.which("llmfit"))
    out("✓ llmfit instaliran." if ok else "⚠ llmfit instaliran, ali nije na PATH-u.")
    return ok


def set_proxy(spine, proxy: str) -> None:
    spine.set_override("setup", "proxy", proxy or "")


def get_proxy(spine) -> str:
    return spine.get_override("setup", "proxy") or ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_preflight.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ragspine/ops/preflight.py tests/test_preflight.py
git commit -m "feat(preflight): winget auto-install (allowlista, UAC, validacija) + pip llmfit + proxy"
```

---

### Task 4: ops/winsvc.py — Windows servis, firewall, ACL (Linux: systemd unit ispis)

**Files:**
- Create: `ragspine/ops/winsvc.py`
- Test: `tests/test_winsvc.py` (novi)

**Interfaces:**
- Produces:
  - `service_commands(exe: str, data_dir: str, port: int) -> list[list[str]]` — ČISTA funkcija: vrati listu komandi (liste argumenata) za: `sc.exe create RAGSPINE binPath= "<exe> serve" start= auto obj= "NT AUTHORITY\LocalService"`, `sc.exe failure RAGSPINE reset= 86400 actions= restart/5000`, `netsh advfirewall firewall add rule name=RAGSPINE dir=in action=allow protocol=TCP localport=<port>`, `icacls <data_dir> /inheritance:r /grant:r "NT AUTHORITY\LocalService:(OI)(CI)F" /grant:r "BUILTIN\Administrators:(OI)(CI)F"`.
  - `install_service(exe: str, data_dir: str, port: int, *, out=print) -> bool` — Windows-only: izvrši `service_commands` redom kroz `run_isolated` (timeout 60 po komandi), stane na prvoj grešci s porukom; ne-Windows: ispiše systemd unit predložak (`systemd_unit(exe, data_dir)`) + upute, vrati False.
  - `systemd_unit(exe: str, data_dir: str) -> str` — tekst unit datoteke (Restart=on-failure, User=komentar).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_winsvc.py  (novi)
from ragspine.ops import winsvc


def test_service_commands_shapes():
    cmds = winsvc.service_commands("C:/rs/ragspine.exe", "C:/data", 8443)
    assert all(isinstance(c, list) for c in cmds)
    flat = [" ".join(c) for c in cmds]
    assert any("sc.exe" in f and "create" in f and "LocalService" in f for f in flat)
    assert any("failure" in f for f in flat)
    assert any("advfirewall" in f and "8443" in f for f in flat)
    assert any("icacls" in f and "C:/data" in f for f in flat)


def test_install_service_windows_executes_all(monkeypatch):
    calls = []
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated",
                        lambda cmd, timeout=60, **kw: calls.append(cmd) or (0, "", ""))
    assert winsvc.install_service("C:/rs.exe", "C:/data", 8443, out=lambda *_: None) is True
    assert len(calls) == len(winsvc.service_commands("C:/rs.exe", "C:/data", 8443))


def test_install_service_stops_on_error(monkeypatch):
    calls = []

    def _run(cmd, timeout=60, **kw):
        calls.append(cmd)
        return (1 if len(calls) == 2 else 0, "", "pristup odbijen")
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Windows")
    monkeypatch.setattr(winsvc, "run_isolated", _run)
    lines = []
    assert winsvc.install_service("C:/rs.exe", "C:/data", 8443, out=lines.append) is False
    assert len(calls) == 2                          # stao na grešci
    assert any("pristup odbijen" in l for l in lines)


def test_install_service_non_windows_prints_systemd(monkeypatch):
    monkeypatch.setattr(winsvc.platform, "system", lambda: "Linux")
    called = []
    monkeypatch.setattr(winsvc, "run_isolated", lambda *a, **k: called.append(1) or (0, "", ""))
    lines = []
    assert winsvc.install_service("/usr/bin/ragspine", "/var/rs", 8443, out=lines.append) is False
    assert not called
    assert any("[Unit]" in l or "systemd" in l.lower() for l in lines)


def test_systemd_unit_content():
    u = winsvc.systemd_unit("/usr/bin/ragspine", "/var/rs")
    assert "[Service]" in u and "Restart=on-failure" in u and "/usr/bin/ragspine serve" in u
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_winsvc.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# ragspine/ops/winsvc.py
"""Windows servis + firewall + ACL za RAGSPINE (spec str.4). Sve komande su
liste argumenata kroz run_isolated — nikad shell string. Servis ide pod
ugrađeni low-priv račun LocalService.
ponytail: custom servisni račun (ragspine_svc) + DPAPI tajne = backlog;
LocalService + file-secret s ACL-om pokriva P3. Nadogradnja: gMSA za domene."""
import platform

from ragspine.core.subproc import run_isolated

_SVC = "RAGSPINE"


def service_commands(exe: str, data_dir: str, port: int) -> list[list[str]]:
    """Komande za kreiranje servisa, recovery, firewall i ACL — redom."""
    return [
        ["sc.exe", "create", _SVC, f"binPath= {exe} serve", "start= auto",
         "obj= NT AUTHORITY\\LocalService"],
        ["sc.exe", "failure", _SVC, "reset= 86400", "actions= restart/5000"],
        ["netsh", "advfirewall", "firewall", "add", "rule", f"name={_SVC}",
         "dir=in", "action=allow", "protocol=TCP", f"localport={port}"],
        ["icacls", data_dir, "/inheritance:r",
         "/grant:r", "NT AUTHORITY\\LocalService:(OI)(CI)F",
         "/grant:r", "BUILTIN\\Administrators:(OI)(CI)F"],
    ]


def systemd_unit(exe: str, data_dir: str) -> str:
    """Predložak systemd unita za ne-Windows servere."""
    return f"""[Unit]
Description=RAGSPINE server
After=network.target

[Service]
ExecStart={exe} serve
Environment=RAGSPINE_DATA_DIR={data_dir}
Restart=on-failure
# User=ragspine   (kreiraj namjenskog korisnika i odkomentiraj)

[Install]
WantedBy=multi-user.target
"""


def install_service(exe: str, data_dir: str, port: int, *, out=print) -> bool:
    """Windows: izvrši komande redom (traži admin/UAC konzolu); stani na grešci.
    Drugi OS: ispiši systemd unit + upute, vrati False."""
    if platform.system() != "Windows":
        out("Windows servis je Windows-only. Za systemd (Linux):")
        out(systemd_unit(exe, data_dir))
        out("Spremi u /etc/systemd/system/ragspine.service pa: systemctl enable --now ragspine")
        return False
    out("Kreiram Windows servis (pokreni iz admin konzole — inače 'pristup odbijen')...")
    for cmd in service_commands(exe, data_dir, port):
        rc, _o, err = run_isolated(cmd, timeout=60)
        if rc != 0:
            out(f"Greška kod: {' '.join(cmd[:3])}... — {err[:200]}")
            return False
    out(f"✓ Servis {_SVC} kreiran (autostart + recovery), firewall otvoren, ACL postavljen.")
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_winsvc.py -v && python -m pytest tests/test_no_cyrillic.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ragspine/ops/winsvc.py tests/test_winsvc.py
git commit -m "feat(net): winsvc — sc.exe servis + firewall + icacls (Linux: systemd predlozak)"
```

---

### Task 5: Stranica 4 — page_mreza + wiring (stage 4, mark_complete pomak, ollama detach)

**Files:**
- Modify: `ragspine/ops/wizard.py` (nova stranica + run() wiring)
- Modify: `ragspine/ops/preflight.py` (`start_ollama` Windows detach flagovi)
- Test: `tests/test_wizard.py`, `tests/test_preflight.py` (dodaj/ažuriraj)

**Interfaces:**
- Consumes: Taskovi 1-4 (`preflight.local_ip/port_free/install_via_winget/set_proxy/get_proxy`, `certs.generate_self_signed/fingerprint_sha256`, `winsvc.install_service`), `tui`, `wizard_state`.
- Produces:
  - `page_mreza(spine, cfg, *, input_fn=input, out=print) -> bool` — tok:
    1. **Bind IP:** `tui.prompt_choice` s [detektirani LAN IP (`local_ip()`), "0.0.0.0 (sve mreže)", "127.0.0.1 (samo ovo računalo)", "Ručni unos"]; ručni → `prompt_text` + `ipaddress.ip_address` validacija s retryjem.
    2. **Port:** `prompt_text` default "8443"; validacija broj 1-65535 + `port_free(bind, port)`; zauzet → upozorenje + retry/nastavi.
    3. **Statička adresa:** ako je `system_state` ip_mode == "dhcp" → upozori (netsh naredba u poruci; izvršavanje = ručno).
    4. **Proxy:** `prompt_text` (prazan = bez proxyja) → `set_proxy`; ako postavljen, ispiši uputu za Ollama servis env (`HTTPS_PROXY`).
    5. **Cert:** `certs.generate_self_signed(data_dir/certs, ips=[bind ili local_ip ako je bind 0.0.0.0], hostnames=["ragspine.local"])` → fingerprint ispis + spomeni `ragspine trust`.
    6. **Spremi:** `spine.set_override("net", ...)` za host/port/cert_path/key_path.
    7. **Servis (preskočivo):** `prompt_yes_no("Instaliraj kao servis (autostart)?", default=False)` → `winsvc.install_service(sys.executable ili sys.argv[0]?, ...)` — koristi `shutil.which("ragspine") or sys.executable + " -m ragspine"`; neuspjeh NE ruši stranicu (samo upozorenje).
    8. Vrati True (svi pod-koraci su preskočivi/degradirajući; False samo na eksplicitni prekid u budućnosti — u P3 stranica uvijek uspije jednom kad se spremi bind+port).
  - `run()`: `if stage < 4:` blok, `set_stage(4)`, `mark_complete` iza (komentar ažuriran: P4 = stranice 5-6); završne poruke ("P3 gotov: ... HTTPS ...").
  - `start_ollama` (preflight): na Windowsu `creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP` umjesto `start_new_session` (koji je POSIX-only); POSIX zadrži `start_new_session=True`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_wizard.py  (dodaj)
def _mreza_mocks(monkeypatch, tmp_path):
    monkeypatch.setattr(wizard.preflight, "local_ip", lambda: "192.168.1.7")
    monkeypatch.setattr(wizard.preflight, "port_free", lambda h, p: True)
    monkeypatch.setattr(wizard.preflight, "system_state",
                        lambda c=None: {"ip_mode": "static"})
    monkeypatch.setattr(wizard.certs, "generate_self_signed",
                        lambda d, ips, hostnames=None: (str(tmp_path / "cert.pem"),
                                                        str(tmp_path / "key.pem")))
    monkeypatch.setattr(wizard.certs, "fingerprint_sha256", lambda p: "AA:BB")


def test_page_mreza_happy_path_saves_overrides(tmp_path, monkeypatch):
    from ragspine.core.spine import init_spine
    s = init_spine(str(tmp_path / "t.db"))
    _mreza_mocks(monkeypatch, tmp_path)

    class _Cfg:
        data_dir = str(tmp_path)
    # izbori: "1" (detektirani IP), port Enter (default 8443), proxy Enter (bez),
    # servis "ne"
    ok = wizard.page_mreza(s, _Cfg(), input_fn=_reader("1", "", "", "ne"),
                           out=lambda *_: None)
    assert ok is True
    assert s.get_override("net", "host") == "192.168.1.7"
    assert s.get_override("net", "port") == "8443"
    assert s.get_override("net", "cert_path", ).endswith("cert.pem")
    assert s.get_override("net", "key_path").endswith("key.pem")


def test_page_mreza_busy_port_retries(tmp_path, monkeypatch):
    from ragspine.core.spine import init_spine
    s = init_spine(str(tmp_path / "t.db"))
    _mreza_mocks(monkeypatch, tmp_path)
    ports = iter([False, True])                     # prvi zauzet, drugi slobodan
    monkeypatch.setattr(wizard.preflight, "port_free", lambda h, p: next(ports))

    class _Cfg:
        data_dir = str(tmp_path)
    lines = []
    ok = wizard.page_mreza(s, _Cfg(), input_fn=_reader("1", "8443", "9000", "", "ne"),
                           out=lines.append)
    assert ok is True
    assert s.get_override("net", "port") == "9000"
    assert any("zauzet" in l.lower() for l in lines)


def test_run_reaches_stage4_and_completes(tmp_path, monkeypatch):
    from ragspine.core.spine import init_spine
    from ragspine.ops import wizard_state as ws
    s = init_spine(str(tmp_path / "t.db"))
    for p in ("page_preduvjeti", "page_operater", "page_model", "page_mreza"):
        monkeypatch.setattr(wizard, p, lambda *a, **k: True)
    wizard.run(s, None, input_fn=_reader(), out=lambda *_: None)
    assert ws.get_stage(s) == 4
    assert ws.is_complete(s) is True
```

Napomena: `test_page_mreza_happy_path_saves_overrides` sadrži namjerno pokvaren redak `s.get_override("net", "cert_path", )` s viškom zareza koji JE validan Python — provjeri stvarni potpis `get_override` (2 argumenta) i piši normalno; marker da se ne prepisuje slijepo.
Postojeći `test_run_reaches_stage3_and_completes` ažuriraj: sad očekuje stage 4 tok (mockaj i `page_mreza`) ili ga zamijeni gornjim stage-4 testom; `test_run_no_complete_when_model_page_cancelled` ostaje (stage ostaje 2). Dodaj i cancel-na-mreži varijantu ako je trivijalno.

```python
# tests/test_preflight.py  (dodaj)
def test_start_ollama_windows_uses_detached_flags(monkeypatch):
    captured = {}

    def _popen(cmd, **kw):
        captured.update(kw)
        return object()
    monkeypatch.setattr(pf.platform, "system", lambda: "Windows")
    monkeypatch.setattr(pf.subprocess, "Popen", _popen)
    monkeypatch.setattr(pf, "ollama_ready", lambda url=None: (True, "radi"))
    assert pf.start_ollama(wait_s=0.1) is True
    assert captured.get("creationflags", 0) != 0
    assert "start_new_session" not in captured
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_wizard.py -k "mreza or stage4" -v && pytest tests/test_preflight.py -k detached -v`
Expected: FAIL — `page_mreza` ne postoji; start_ollama nema creationflags

- [ ] **Step 3: Write minimal implementation**

`wizard.py` — import `certs` i `winsvc` na vrh (`from ragspine.ops import certs, preflight, tui, winsvc, wizard_state`), `import ipaddress`, `import shutil`, `import sys`, pa:

```python
def page_mreza(spine, cfg, *, input_fn=input, out=print) -> bool:
    """Stranica 4: bind IP + port, cert/HTTPS, proxy, servis (preskočivo)."""
    tui.print_header("4/6  Mreža + HTTPS + servis", out=out)
    lan = preflight.local_ip()

    # 1) bind IP
    choices = [f"{lan} (detektirani LAN IP)", "0.0.0.0 (sve mreže)",
               "127.0.0.1 (samo ovo računalo)", "Ručni unos"]
    idx = tui.prompt_choice("Na kojoj adresi server sluša?", choices,
                            default=0, input_fn=input_fn, out=out)
    if idx == 0:
        bind = lan
    elif idx == 1:
        bind = "0.0.0.0"
    elif idx == 2:
        bind = "127.0.0.1"
    else:
        while True:
            bind = tui.prompt_text("IP adresa", input_fn=input_fn, out=out)
            try:
                ipaddress.ip_address(bind)
                break
            except ValueError:
                out("Neispravna IP adresa.")

    # 2) port
    while True:
        raw = tui.prompt_text("Port", default="8443", input_fn=input_fn, out=out)
        try:
            port = int(raw)
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            out("Port mora biti broj 1-65535.")
            continue
        if not preflight.port_free(bind if bind != "0.0.0.0" else "127.0.0.1", port):
            out(f"Port {port} je zauzet — odaberi drugi.")
            continue
        break

    # 3) statička adresa (upozorenje, ne izvršavamo netsh set)
    if preflight.system_state(cfg).get("ip_mode") == "dhcp":
        out("⚠ Računalo je na DHCP-u — adresa se može promijeniti i klijenti gube vezu.")
        out("  Postavi statičku: netsh interface ip set address (ili rezervacija na routeru).")

    # 4) proxy
    proxy = tui.prompt_text("HTTP proxy (prazno = bez proxyja)",
                            input_fn=input_fn, out=out)
    preflight.set_proxy(spine, proxy)
    if proxy:
        out(f"  Za Ollama servis postavi env: HTTPS_PROXY={proxy}")

    # 5) cert
    cert_ip = lan if bind == "0.0.0.0" else bind
    cert_dir = str(Path(getattr(cfg, "data_dir", ".")) / "certs")
    cert, key = certs.generate_self_signed(cert_dir, ips=[cert_ip],
                                           hostnames=["ragspine.local"])
    out(f"HTTPS certifikat: {cert}")
    out(f"  SHA256: {certs.fingerprint_sha256(cert)}")
    out("  Na klijente ga instaliraj naredbom: ragspine trust")

    # 6) spremi net postavke
    spine.set_override("net", "host", bind)
    spine.set_override("net", "port", str(port))
    spine.set_override("net", "cert_path", cert)
    spine.set_override("net", "key_path", key)
    out(f"✓ Server će služiti na https://{cert_ip}:{port}")

    # 7) servis (preskočivo; neuspjeh ne ruši stranicu)
    if tui.prompt_yes_no("Instaliraj kao servis (autostart)?", default=False,
                         input_fn=input_fn, out=out):
        exe = shutil.which("ragspine") or f"{sys.executable} -m ragspine"
        if not winsvc.install_service(exe, getattr(cfg, "data_dir", "."), port, out=out):
            out("⚠ Servis nije instaliran — možeš ponoviti kasnije (admin konzola).")
    return True
```

`run()` — iza stage<3 bloka:

```python
        if stage < 4:
            if not page_mreza(spine, cfg, input_fn=input_fn, out=out):
                out("Setup prekinut na mreži. Pokreni ponovno za nastavak.")
                return
            wizard_state.set_stage(spine, 4)
```

Završni blok: komentar „P3 pokriva stranice 1-4; mark_complete iza ZADNJE implementirane (P4 = 5-6)" + poruka `"P3 gotov: preduvjeti + operater + model + mreža/HTTPS. Setup je dovršen — web sučelje je dostupno."` / `"Stranice 5-6 (mape, sažetak) slijede u P4."`.

`preflight.start_ollama` — zamijeni Popen poziv:

```python
    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "stdin": subprocess.DEVNULL}
    if platform.system() == "Windows":
        # POSIX start_new_session je no-op na Windowsu — treba DETACHED_PROCESS
        # da daemon preživi zatvaranje wizard konzole.
        kwargs["creationflags"] = (subprocess.DETACHED_PROCESS
                                   | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(["ollama", "serve"], **kwargs)
    except OSError:
        return False
```

Pazi: `subprocess.DETACHED_PROCESS` ne postoji na POSIX-u — pristupaj mu SAMO unutar Windows grane (getattr nije potreban jer je atribut prisutan samo na Windows buildu Pythona — koristi `getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)` da test na Linuxu s mockanim platform.system()=="Windows" ne padne na AttributeError).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_wizard.py tests/test_preflight.py -v && python -m pytest tests/test_no_cyrillic.py -q`
Expected: PASS

- [ ] **Step 5: Puni suite**

Run: `python -m pytest -q`
Expected: zeleno

- [ ] **Step 6: Commit**

```bash
git add ragspine/ops/wizard.py ragspine/ops/preflight.py tests/test_wizard.py tests/test_preflight.py
git commit -m "feat(setup): stranica 4 (mreža+HTTPS+servis) — stage 4, mark_complete iza; ollama detach na Windowsu"
```

---

## Self-Review (autor)

**Spec coverage (str.4):**
- Bind IP + port izbor + provjera zauzetosti OVDJE + više NIC-ova (izbor: LAN/0.0.0.0/loopback/ručni) → Task 1, 5. ✓
- Potvrda statičke adrese (detekcija iz str.1, netsh naredba u poruci) → Task 5 korak 3. ✓ (izvršavanje `netsh set` = ručno; svjesno)
- SAN cert za odabrani IP/hostname; `ragspine trust` samo javni cert + fingerprint → Task 2, 5. ✓ (AD CS/GPO = napomena)
- Windows Service autostart/recovery + ACL + firewall → Task 4, 5. ✓ (LocalService umjesto custom računa — dokumentirano odstupanje)
- Ključevi izvan DB-a: postojeći file-secret 0600 + icacls u ACL komandi; DPAPI = backlog (dokumentirano). Export/restore upute idu u P4 stranicu 6 (backup sekcija speca).
- `ragspine serve` HTTPS (uvicorn SSL) → Task 1. ✓
- Winget auto-install (allowlista, UAC, validacija, PATH poruka) → Task 3. ✓; proxy polje → Task 3, 5. ✓
- start_ollama Windows detach → Task 5. ✓

**Placeholder scan:** čisto; jedini marker je eksplicitno označeni pokvareni redak u Task 5 Step 1.

**Type consistency:** `local_ip()/port_free(host,port)`, `WINGET_IDS/install_via_winget(key)/install_llmfit()/set_proxy/get_proxy`, `certs.generate_self_signed(out_dir, ips, hostnames, days)->(str,str)/fingerprint_sha256(path)->str`, `winsvc.service_commands(exe,data_dir,port)/install_service(...)->bool/systemd_unit(exe,data_dir)->str`, `page_mreza(spine,cfg,*,input_fn,out)->bool`, `_net_overrides(spine,cfg)->(host,port,cert,key)` — usklađeno kroz taskove.

## Runnable check

`pytest tests/test_preflight.py tests/test_certs.py tests/test_winsvc.py tests/test_wizard.py tests/test_cli.py tests/test_no_cyrillic.py -q` zelen + `python -m pytest -q` zelen.
