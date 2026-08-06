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
