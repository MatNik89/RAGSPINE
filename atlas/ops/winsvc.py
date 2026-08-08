# atlas/ops/winsvc.py
"""Windows servis (WinSW wrapper) + firewall + ACL; systemd unit za
Linux/macOS. ATLAS (spec 2026-08-08: pravi 24/7 servis). Sve subprocess
komande idu kroz run_isolated — nikad shell string. Servis ide pod ugrađeni
low-priv račun LocalService.
ponytail: custom servisni račun (atlas_svc) + DPAPI tajne = backlog;
LocalService + file-secret s ACL-om pokriva P3. Nadogradnja: gMSA za domene."""
import os
import platform
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

from atlas.core.subproc import run_isolated

_SVC = "ATLAS"
# Pinnan release (ne latest) — determinizam. Nadogradnja: ručno podigni broj.
WINSW_URL = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe"


def download_winsw(dest_path, *, urlopen=urllib.request.urlopen, out=print) -> bool:
    """Skini WinSW.exe u dest_path. Idempotentno: postojeći file = True bez
    mreže. Atomski (tmp pa os.replace) — polu-skinuti exe ne smije završiti
    kao 'instaliran'."""
    dest = Path(dest_path)
    if dest.exists():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        with urlopen(WINSW_URL, timeout=120) as r:
            data = r.read()
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    except Exception as e:
        out(f"✗ WinSW download nije uspio: {e}")
        tmp.unlink(missing_ok=True)
        return False
    return True


def winsw_xml(exe: str, data_dir: str, port: int) -> str:
    """WinSW servisni XML — čista funkcija. exe smije sadržavati razmake
    (Program Files): stoji u vlastitom XML elementu, bez potrebe za
    navodnicima (za razliku od sc.exe binPath tokenizacije)."""
    log_dir = str(Path(data_dir) / "logs")
    exe_x, dd_x, ld_x = escape(str(exe)), escape(str(data_dir)), escape(log_dir)
    restarts = "".join('  <onfailure action="restart" delay="5 sec"/>\n' for _ in range(3))
    return f"""<service>
  <id>ATLAS</id>
  <name>ATLAS</name>
  <description>ATLAS server — RAG asistent za uredsko poslovanje.</description>
  <executable>{exe_x}</executable>
  <arguments>serve</arguments>
  <env name="ATLAS_DATA_DIR" value="{dd_x}"/>
  <env name="ATLAS_PORT" value="{port}"/>
  <logpath>{ld_x}</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>4</keepFiles>
  </log>
{restarts}  <onfailure action="none"/>
  <stoptimeout>15 sec</stoptimeout>
  <serviceaccount>
    <username>NT AUTHORITY\\LocalService</username>
  </serviceaccount>
</service>
"""


def service_commands(data_dir: str, port: int) -> list[list[str]]:
    """Firewall + ACL komande (sc.exe create/failure zamijenjen WinSW-om —
    on kreira i nadzire pravi SCM servis)."""
    return [
        ["netsh", "advfirewall", "firewall", "add", "rule", f"name={_SVC}",
         "dir=in", "action=allow", "protocol=TCP", f"localport={port}"],
        ["icacls", data_dir, "/inheritance:r",
         "/grant:r", "NT AUTHORITY\\LocalService:(OI)(CI)F",
         "/grant:r", "BUILTIN\\Administrators:(OI)(CI)F"],
    ]


def systemd_unit(exe: str, data_dir: str) -> str:
    """Predložak systemd unita za ne-Windows servere."""
    return f"""[Unit]
Description=ATLAS server
After=network.target

[Service]
ExecStart={exe} serve
Environment=ATLAS_DATA_DIR={data_dir}
Restart=on-failure
# User=atlas   (kreiraj namjenskog korisnika i odkomentiraj)

[Install]
WantedBy=multi-user.target
"""


def _install_systemd(exe: str, data_dir: str, unit_path: str, *, out=print) -> bool:
    unit = systemd_unit(exe, data_dir)
    try:
        Path(unit_path).write_text(unit, encoding="utf-8")
    except PermissionError:
        out(f"Nemam prava pisati {unit_path}. Spremi ručno ovaj sadržaj pa pokreni:")
        out(unit)
        out(f"sudo cp <datoteka> {unit_path} && sudo systemctl daemon-reload "
            "&& sudo systemctl enable --now atlas")
        return False
    rc, _o, err = run_isolated(["systemctl", "daemon-reload"], timeout=30)
    if rc != 0:
        out(f"✗ systemctl daemon-reload nije uspio: {err[:200]}")
        return False
    rc, _o, err = run_isolated(["systemctl", "enable", "--now", "atlas"], timeout=30)
    if rc != 0:
        out(f"✗ systemctl enable --now nije uspio: {err[:200]}")
        return False
    out(f"✓ Servis atlas instaliran i pokrenut ({unit_path}).")
    return True


def install_service(exe: str, data_dir: str, port: int, *, out=print,
                     urlopen=urllib.request.urlopen,
                     unit_path: str = "/etc/systemd/system/atlas.service") -> bool:
    """Windows: WinSW pretvara `atlas serve` (konzolna app) u pravi SCM
    servis — skida WinSW, piše XML, install+start, pa firewall+ACL (isto
    kao prije). Ne-Windows: zapiši systemd unit (PermissionError → upute)."""
    if platform.system() != "Windows":
        return _install_systemd(exe, data_dir, unit_path, out=out)

    service_dir = Path(data_dir) / "service"
    winsw_exe = service_dir / "atlas-service.exe"
    if not download_winsw(winsw_exe, urlopen=urlopen, out=out):
        return False
    xml_path = service_dir / "atlas-service.xml"
    try:
        xml_path.write_text(winsw_xml(exe, data_dir, port), encoding="utf-8")
    except OSError as e:
        out(f"✗ Pisanje {xml_path} nije uspjelo: {e}")
        return False

    for step, cmd in (("install", [str(winsw_exe), "install"]),
                       ("start", [str(winsw_exe), "start"])):
        rc, _o, err = run_isolated(cmd, timeout=60)
        if rc != 0:
            out(f"✗ WinSW {step} nije uspio: {err[:200]}")
            return False

    out("Postavljam firewall pravilo i ACL na podatkovnu mapu...")
    for cmd in service_commands(data_dir, port):
        rc, _o, err = run_isolated(cmd, timeout=60)
        if rc != 0:
            out(f"Greška kod: {' '.join(cmd[:3])}... — {err[:200]}")
            return False

    out(f"✓ Servis {_SVC} instaliran i pokrenut (WinSW, port {port}, "
        f"logovi u {data_dir}/logs).")
    out("Napomena: za auto-start nakon nestanka struje uključi u BIOS-u "
        "'Restore on AC Power' (jednokratno, na ovom stroju).")
    return True


def uninstall_service(data_dir: str, *, out=print,
                       unit_path: str = "/etc/systemd/system/atlas.service") -> bool:
    """Windows: atlas-service.exe stop + uninstall (ako exe postoji).
    Ne-Windows: ispiši systemd naredbe za uklanjanje."""
    if platform.system() != "Windows":
        out(f"Zaustavi i ukloni servis: sudo systemctl disable --now atlas "
            f"&& sudo rm {unit_path} && sudo systemctl daemon-reload")
        return False
    winsw_exe = Path(data_dir) / "service" / "atlas-service.exe"
    if not winsw_exe.exists():
        out(f"✗ {winsw_exe} ne postoji — servis vjerojatno nije instaliran preko ATLAS-a.")
        return False
    ok = True
    for step, cmd in (("stop", [str(winsw_exe), "stop"]),
                       ("uninstall", [str(winsw_exe), "uninstall"])):
        rc, _o, err = run_isolated(cmd, timeout=60)
        if rc != 0:
            out(f"✗ WinSW {step} nije uspio: {err[:200]}")
            ok = False
    if ok:
        out(f"✓ Servis {_SVC} uklonjen.")
    return ok


def service_status(*, out=print) -> str:
    """Windows: 'sc.exe query ATLAS' parsiranje. Ne-Windows: 'systemctl
    is-active atlas'. Vraća: running / stopped / inactive / not-installed."""
    if platform.system() != "Windows":
        rc, stdout, _err = run_isolated(["systemctl", "is-active", "atlas"], timeout=15)
        state = stdout.strip()
        if rc == 0 and state == "active":
            return "running"
        if state in ("inactive", "failed"):
            return "inactive"
        return "not-installed"
    rc, stdout, _err = run_isolated(["sc.exe", "query", _SVC], timeout=15)
    if rc != 0:
        return "not-installed"
    if "RUNNING" in stdout:
        return "running"
    if "STOPPED" in stdout:
        return "stopped"
    return "not-installed"
