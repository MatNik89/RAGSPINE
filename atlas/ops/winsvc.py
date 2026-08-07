# atlas/ops/winsvc.py
"""Windows servis + firewall + ACL za ATLAS (spec str.4). Sve komande su
liste argumenata kroz run_isolated — nikad shell string. Servis ide pod
ugrađeni low-priv račun LocalService.
ponytail: custom servisni račun (atlas_svc) + DPAPI tajne = backlog;
LocalService + file-secret s ACL-om pokriva P3. Nadogradnja: gMSA za domene."""
import platform

from atlas.core.subproc import run_isolated

_SVC = "ATLAS"


def service_commands(exe: str, data_dir: str, port: int) -> list[list[str]]:
    """Komande za kreiranje servisa, recovery, firewall i ACL — redom.
    sc.exe traži parove ključ=vrednost kao dva odvojena tokena; list2cmdline
    na Windowsu ne navoduje pojedine tokene, samo cijelo buildirane naredbe."""
    return [
        ["sc.exe", "create", _SVC, "binPath=", f"{exe} serve", "start=", "auto",
         "obj=", "NT AUTHORITY\\LocalService"],
        ["sc.exe", "failure", _SVC, "reset=", "86400", "actions=", "restart/5000"],
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


def install_service(exe: str, data_dir: str, port: int, *, out=print) -> bool:
    """Windows: firewall + ACL se STVARNO izvršavaju; sc.exe create/failure se
    SAMO ispisuju (ne izvršavaju) jer `atlas serve` je konzolna aplikacija
    bez Windows service-protokola — sc.exe create bi kreirao servis koji odmah
    umire (greška 1053). Vraća False jer servis nije stvarno kreiran.
    ponytail: pravi Windows servis traži service wrapper (WinSW ili NSSM) koji
    prevede konzolnu app u SCM protokol — backlog. LocalService + ACL iz ove
    funkcije i dalje vrijede kad wrapper stigne.
    Drugi OS: ispiši systemd unit + upute, vrati False."""
    if platform.system() != "Windows":
        out("Windows servis je Windows-only. Za systemd (Linux):")
        out(systemd_unit(exe, data_dir))
        out("Spremi u /etc/systemd/system/atlas.service pa: systemctl enable --now atlas")
        return False
    create_cmd, failure_cmd, firewall_cmd, icacls_cmd = service_commands(exe, data_dir, port)
    out("⚠ `atlas serve` je konzolna aplikacija, ne pravi Windows servis (SCM protokol) —")
    out("  sc.exe create bi kreirao servis koji odmah padne (greška 1053).")
    out("  Za pravi servis treba wrapper (WinSW ili NSSM — backlog). Ručne komande:")
    out("  " + " ".join(create_cmd))
    out("  " + " ".join(failure_cmd))
    out("Postavljam firewall pravilo i ACL na podatkovnu mapu...")
    for cmd in (firewall_cmd, icacls_cmd):
        rc, _o, err = run_isolated(cmd, timeout=60)
        if rc != 0:
            out(f"Greška kod: {' '.join(cmd[:3])}... — {err[:200]}")
            return False
    out(f"✓ Firewall otvoren (port {port}) i ACL na {data_dir} postavljen. "
        f"Servis {_SVC} NIJE kreiran — instaliraj wrapper pa ručno pokreni gornje komande.")
    return False
