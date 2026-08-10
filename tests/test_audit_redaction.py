"""Audit redakcija tajni (E): agent_execute logira proizvoljne args -> tajna ne
smije procuriti u plaintext audit_log."""
from atlas.core.security import redact_secrets


def test_redacts_json_secret_keys():
    out = redact_secrets('{"token": "abc123", "naslov": "Bok"}')
    assert "abc123" not in out and "[REDACTED]" in out and "Bok" in out


def test_redacts_kv_and_case_insensitive():
    assert "tajna" not in redact_secrets("password=tajna123")
    assert "xY" not in redact_secrets("API_KEY: xY9zzz")
    assert "s3cr" not in redact_secrets('"sign_key": "s3cret"')


def test_redacts_url_credentials():
    out = redact_secrets("mailto://ana:LOZINKA@mail.hr")
    assert "LOZINKA" not in out and "ana" in out and "mail.hr" in out


def test_keeps_plain_detail():
    assert redact_secrets("client:5 dodaj_klijenta") == "client:5 dodaj_klijenta"


def test_audit_row_is_redacted(spine, cfg):
    spine.audit("ana", "agent_execute", "posalji", '{"token":"SUPERTAJNA","x":1}')
    row = spine.read().execute(
        "SELECT detail FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    assert "SUPERTAJNA" not in row["detail"] and "[REDACTED]" in row["detail"]
