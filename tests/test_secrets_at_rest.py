"""B: secrets-at-rest — messaging_target (nosi SMTP lozinku) šifriran u bazi;
backup ne curi kredencijal. secretbox podnosi stare plaintext zapise."""
from fastapi.testclient import TestClient

from atlas.business import secretbox
from atlas.web.api import create_app
from atlas.web.deps import add_user
from tests.conftest import complete_setup


def _admin(spine, cfg):
    c = TestClient(create_app(spine, cfg))
    add_user(spine, "gazda", "pw")
    complete_setup(spine)
    tok = c.post("/auth/login", json={"username": "gazda", "password": "pw"}).json()["token"]
    return c, {"Authorization": f"Bearer {tok}"}


def test_messaging_target_stored_encrypted(spine, cfg):
    c, h = _admin(spine, cfg)
    with spine.write() as conn:
        cid = conn.execute("INSERT INTO clients(name) VALUES('Pekara')").lastrowid
    secret = "mailto://user:TAJNA123@mail.example.com"
    r = c.post(f"/clients/{cid}/messaging", headers=h,
               json={"consent": 1, "channel": "apprise", "target": secret})
    assert r.status_code == 200
    stored = spine.read().execute(
        "SELECT messaging_target FROM clients WHERE id=?", (cid,)).fetchone()["messaging_target"]
    assert stored.startswith("enc:") and "TAJNA123" not in stored  # backup ne curi lozinku
    assert secretbox.decrypt(stored, cfg) == secret  # dešifriranje vraća original


def test_send_to_client_decrypts_target(spine, cfg):
    from atlas.business import messaging
    with spine.write() as conn:
        cid = conn.execute("INSERT INTO clients(name) VALUES('X')").lastrowid
        enc = secretbox.encrypt("mailto://a@b.com", cfg)
        conn.execute("UPDATE clients SET messaging_consent=1, messaging_channel='mail', "
                     "messaging_target=? WHERE id=?", (enc, cid))
    res = messaging.send_to_client(spine, cfg, cid, "Test", "Body", dry_run=True)
    assert res["status"] == "dry_run"  # dešifrirani target prošao scheme-check


def test_old_plaintext_target_still_works(spine, cfg):
    # back-compat: stari (nešifrirani) zapis se i dalje koristi (secretbox fallback)
    from atlas.business import messaging
    with spine.write() as conn:
        cid = conn.execute("INSERT INTO clients(name) VALUES('Y')").lastrowid
        conn.execute("UPDATE clients SET messaging_consent=1, messaging_channel='mail', "
                     "messaging_target='mailto://c@d.com' WHERE id=?", (cid,))
    res = messaging.send_to_client(spine, cfg, cid, "T", "B", dry_run=True)
    assert res["status"] == "dry_run"
