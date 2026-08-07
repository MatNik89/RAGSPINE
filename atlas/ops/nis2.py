"""NIS2 compliance checklist backed by the memory table (user='system')."""

CHECKLIST: list[dict] = [
    {"id": "backup", "control": "Sigurnosne kopije",
     "description": "Redovite automatizirane sigurnosne kopije podataka uz testiranje obnove."},
    {"id": "encryption_at_rest", "control": "Enkripcija u mirovanju",
     "description": "Podaci na disku enkriptirani (LUKS/TDE)."},
    {"id": "mfa", "control": "Višefaktorska autentifikacija",
     "description": "MFA za sve administrativne i udaljene pristupe."},
    {"id": "patch_mgmt", "control": "Upravljanje zakrpama",
     "description": "Redovito ažuriranje OS-a i ovisnosti."},
    {"id": "logging", "control": "Zapisivanje i nadzor",
     "description": "Centralizirano logiranje i audit trag."},
    {"id": "incident_plan", "control": "Plan odgovora na incidente",
     "description": "Dokumentiran i testiran plan odgovora na sigurnosne incidente."},
    {"id": "access_control", "control": "Kontrola pristupa",
     "description": "Načelo najmanjih privilegija, RBAC."},
    {"id": "network_segmentation", "control": "Segmentacija mreže",
     "description": "Odvajanje kritičnih sustava od javne mreže."},
    {"id": "vendor_mgmt", "control": "Upravljanje dobavljačima",
     "description": "Sigurnosna procjena trećih strana/dobavljača."},
    {"id": "awareness", "control": "Sigurnosna svijest",
     "description": "Redovita edukacija zaposlenika o sigurnosti."},
    {"id": "bcp", "control": "Plan kontinuiteta poslovanja",
     "description": "Plan kontinuiteta poslovanja i oporavka od katastrofe."},
    {"id": "data_encryption", "control": "Enkripcija podataka u prijenosu",
     "description": "TLS/HTTPS za sve podatke u prijenosu."},
]


def report(spine) -> list[dict]:
    out = []
    for item in CHECKLIST:
        row = spine.read().execute(
            "SELECT value FROM memory WHERE user=? AND key=?",
            ("system", f"nis2.{item['id']}")).fetchone()
        out.append({"id": item["id"], "control": item["control"],
                    "status": row["value"] if row else "nepoznato"})
    return out


def set_status(spine, control_id: str, status: str) -> None:
    with spine.write() as c:
        c.execute(
            """INSERT INTO memory(user,key,value) VALUES(?,?,?)
               ON CONFLICT(user,key) DO UPDATE SET value=excluded.value""",
            ("system", f"nis2.{control_id}", status))


def smart() -> dict:
    return {"status": "stub"}


def lynis() -> dict:
    return {"status": "stub"}


def nmap() -> dict:
    return {"status": "stub"}
