# atlas/ops/winsvc.py
"""Windows service (WinSW wrapper) + firewall + ACL; systemd unit for
Linux/macOS. ATLAS (spec 2026-08-08: a real 24/7 service). All subprocess
commands go through run_isolated — never a shell string. The service runs
under the built-in low-priv LocalService account.
ponytail: custom service account (atlas_svc) + DPAPI secrets = backlog;
LocalService + a file-secret with an ACL covers P3. Upgrade: gMSA for
domains."""
import os
import platform
import shutil
import sys
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

from atlas.core.subproc import run_isolated
from atlas.ops import winpath

_SVC = "ATLAS"
# Pinned release (not latest) — determinism. Upgrade: bump the number manually.
WINSW_URL = "https://github.com/winsw/winsw/releases/download/v2.12.0/WinSW-x64.exe"


def resolve_atlas_cmd() -> tuple[str, list[str]]:
    """(executable, args) for the `atlas serve` service — a real atlas.exe if
    it is on PATH, otherwise the current python interpreter + `-m atlas` (the
    same pattern as wizard.launch_now). SPLIT, not joined into one string —
    WinSW <executable> must be a real path to a SINGLE file (review finding:
    'python -m atlas' as one string looks for a nonexistent file of that name,
    so the service never starts when atlas is not on PATH)."""
    exe = shutil.which("atlas")
    if exe:
        return exe, ["serve"]
    return sys.executable, ["-m", "atlas", "serve"]


def download_winsw(dest_path, *, urlopen=urllib.request.urlopen, out=print) -> bool:
    """Download WinSW.exe to dest_path. Idempotent: an existing file = True
    without network. Atomic (tmp then os.replace) — a half-downloaded exe must
    not end up 'installed'."""
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


def _env_block(pairs: dict[str, str], *, xml: bool) -> str:
    """Shared env serialization for winsw_xml/systemd_unit."""
    if xml:
        return "".join(f'  <env name="{escape(k)}" value="{escape(str(v))}"/>\n'
                       for k, v in pairs.items())
    return "".join(f"Environment={k}={v}\n" for k, v in pairs.items())


def winsw_xml(exe: str, args: list[str], data_dir: str, port: int, *,
             extra_env: dict[str, str] | None = None) -> str:
    """WinSW service XML — a pure function. exe/args SEPARATE: <executable>
    must be a real path to a single file (Program Files may contain spaces — it
    sits in its own XML element, without needing quotes, unlike sc.exe binPath
    tokenization), <arguments> carries the rest of the command line (e.g.
    '-m atlas serve')."""
    log_dir = str(Path(data_dir) / "logs")
    args_str = " ".join(args)
    exe_x, args_x = escape(str(exe)), escape(args_str)
    ld_x = escape(log_dir)
    restarts = "".join('  <onfailure action="restart" delay="5 sec"/>\n' for _ in range(3))
    env_pairs = {"ATLAS_DATA_DIR": str(data_dir), "ATLAS_PORT": str(port), **(extra_env or {})}
    envs = _env_block(env_pairs, xml=True)
    return f"""<service>
  <id>ATLAS</id>
  <name>ATLAS</name>
  <description>ATLAS server — RAG asistent za uredsko poslovanje.</description>
  <executable>{exe_x}</executable>
  <arguments>{args_x}</arguments>
{envs}  <logpath>{ld_x}</logpath>
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
    """Firewall + ACL commands (sc.exe create/failure replaced by WinSW — it
    creates and supervises the real SCM service)."""
    return [
        ["netsh", "advfirewall", "firewall", "add", "rule", f"name={_SVC}",
         "dir=in", "action=allow", "protocol=TCP", f"localport={port}"],
        ["icacls", data_dir, "/inheritance:r",
         "/grant:r", "NT AUTHORITY\\LocalService:(OI)(CI)F",
         "/grant:r", "BUILTIN\\Administrators:(OI)(CI)F"],
    ]


def systemd_unit(exe: str, args: list[str], data_dir: str, *,
                 extra_env: dict[str, str] | None = None) -> str:
    """systemd unit template for non-Windows servers. exe+args are joined into
    one ExecStart string — systemd word-splits ExecStart, so 'python -m
    atlas serve' works correctly (unlike WinSW <executable>)."""
    cmd = " ".join([str(exe), *args])
    env_pairs = {"ATLAS_DATA_DIR": data_dir, **(extra_env or {})}
    envs = _env_block(env_pairs, xml=False)
    return f"""[Unit]
Description=ATLAS server
After=network.target

[Service]
ExecStart={cmd}
{envs}Restart=on-failure
# User=atlas   (kreiraj namjenskog korisnika i odkomentiraj)

[Install]
WantedBy=multi-user.target
"""


def _install_systemd(exe: str, args: list[str], data_dir: str, unit_path: str, *,
                     extra_env: dict[str, str] | None = None, out=print) -> bool:
    unit = systemd_unit(exe, args, data_dir, extra_env=extra_env)
    try:
        Path(unit_path).write_text(unit, encoding="utf-8")
    except OSError as e:
        # OSError (not just PermissionError) — macOS has no /etc/systemd/system,
        # so write_text raises FileNotFoundError, which otherwise propagates and
        # crashes the wizard page instead of printing instructions (review finding).
        out(f"Ne mogu zapisati {unit_path} ({e}). Spremi ručno ovaj sadržaj pa pokreni:")
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


def _looks_like_user_profile_path(exe: str) -> bool:
    """LocalService usually lacks read rights under a user profile
    (C:\\Users\\...) — detection for a warning only, no blocking."""
    return "\\users\\" in str(exe).lower().replace("/", "\\")


def _extra_env(data_dir: str, mount_roots: list[str] | None) -> dict[str, str]:
    """Service env that LocalService/systemd would otherwise not see: network
    shares (folders are fail-closed without ATLAS_MOUNT_ROOTS) and
    TESSDATA_PREFIX (the HKCU persist from preflight.ensure_traineddata does not
    apply to LocalService — a different logon session, a different registry
    hive)."""
    env: dict[str, str] = {}
    if mount_roots:
        env["ATLAS_MOUNT_ROOTS"] = ",".join(mount_roots)
    tessdata = os.environ.get("TESSDATA_PREFIX") or winpath.get_user_env("TESSDATA_PREFIX") or ""
    if not tessdata:
        cand = Path(data_dir) / "tessdata"
        if cand.is_dir():
            tessdata = str(cand)
    if tessdata:
        env["TESSDATA_PREFIX"] = tessdata
    return env


def install_service(exe: str, args: list[str], data_dir: str, port: int, *, out=print,
                     urlopen=urllib.request.urlopen,
                     unit_path: str = "/etc/systemd/system/atlas.service",
                     mount_roots: list[str] | None = None) -> bool:
    """Windows: WinSW turns `atlas serve` (a console app) into a real SCM
    service — downloads WinSW, writes the XML, install+start, then firewall+ACL
    (same as before). Non-Windows: write the systemd unit (OSError -> instructions).
    exe/args SPLIT — see resolve_atlas_cmd. mount_roots goes into the
    ATLAS_MOUNT_ROOTS of the service env (the service does not share the process
    the wizard/CLI started); TESSDATA_PREFIX is added automatically if present.
    Idempotent: an existing service (in any state) is removed before
    reinstallation instead of letting WinSW install fail on 'already exists'."""
    extra_env = _extra_env(data_dir, mount_roots)
    if platform.system() != "Windows":
        return _install_systemd(exe, args, data_dir, unit_path, extra_env=extra_env, out=out)

    service_dir = Path(data_dir) / "service"
    winsw_exe = service_dir / "atlas-service.exe"
    if not download_winsw(winsw_exe, urlopen=urlopen, out=out):
        return False

    status = service_status()
    if status != "not-installed":
        out(f"Servis {_SVC} već postoji ({status}) — uklanjam prije reinstalacije...")
        uninstall_service(data_dir, out=out)

    if _looks_like_user_profile_path(exe):
        out("⚠ Exe je pod korisničkim profilom (C:\\Users\\...) — LocalService račun "
            "možda nema prava čitanja (start bi pao s greškom 1053). Preporuka: kopiraj "
            "ATLAS na sistemsku lokaciju (npr. C:\\Program Files\\ATLAS) prije instalacije.")

    xml_path = service_dir / "atlas-service.xml"
    try:
        xml_path.write_text(winsw_xml(exe, args, data_dir, port, extra_env=extra_env),
                            encoding="utf-8")
    except OSError as e:
        out(f"✗ Pisanje {xml_path} nije uspjelo: {e}")
        return False

    for step, cmd in (("install", [str(winsw_exe), "install"]),
                       ("start", [str(winsw_exe), "start"])):
        rc, _o, err = run_isolated(cmd, timeout=60)
        if rc != 0:
            out(f"✗ WinSW {step} nije uspio: {err[:200]}")
            return False

    bios_note = ("Napomena: za auto-start nakon nestanka struje uključi u BIOS-u "
                "'Restore on AC Power' (jednokratno, na ovom stroju).")
    out("Postavljam firewall pravilo i ACL na podatkovnu mapu...")
    for cmd in service_commands(data_dir, port):
        rc, _o, err = run_isolated(cmd, timeout=60)
        if rc != 0:
            # The service WORKS (WinSW install+start succeeded) — only the
            # firewall/ACL step did not pass. It must not sound like a total
            # failure (review finding), otherwise the caller (wizard) relays the
            # wrong message onward.
            out(f"⚠ Servis {_SVC} RADI (WinSW), ali korak {' '.join(cmd[:3])}... nije "
                f"prošao: {err[:200]} — postavi ručno (administratorski cmd/PowerShell).")
            out(bios_note)
            return True

    out(f"✓ Servis {_SVC} instaliran i pokrenut (WinSW, port {port}, "
        f"logovi u {data_dir}/logs).")
    if mount_roots:
        out("Napomena: mape registrirane NAKON ove instalacije traže ponovni "
            "`atlas servis install` da servis vidi novu ATLAS_MOUNT_ROOTS listu.")
    out(bios_note)
    return True


def uninstall_service(data_dir: str, *, out=print,
                       unit_path: str = "/etc/systemd/system/atlas.service") -> bool:
    """Windows: atlas-service.exe stop + uninstall (if the exe exists).
    Non-Windows: print the systemd commands for removal."""
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
    """Windows: parsing 'sc.exe query ATLAS'. Non-Windows: 'systemctl
    is-active atlas'. Returns: running / stopped / inactive / not-installed.
    START_PENDING counts as running, STOP_PENDING as stopped — the caller
    (e.g. launch_now) must not treat a transitional state as 'no service'."""
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
    if "RUNNING" in stdout or "START_PENDING" in stdout:
        return "running"
    if "STOPPED" in stdout or "STOP_PENDING" in stdout:
        return "stopped"
    return "not-installed"
