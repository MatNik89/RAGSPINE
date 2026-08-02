from fastapi.testclient import TestClient

from ragspine.business import obveze
from ragspine.web.api import create_app
from ragspine.web.deps import add_user


def _seed(spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name, oib, pdv_status) VALUES ('Zebra', '1', 'u sustavu pdv')")
        c.execute("INSERT INTO clients(name, oib, pdv_status) VALUES ('Alfa', '2', 'u sustavu pdv')")
        c.execute("INSERT INTO clients(name, oib, pdv_status) VALUES ('Beta', '3', 'nije u pdvu')")


def test_only_pdv_clients(spine):
    _seed(spine)
    obveze.ensure_period(spine, "PDV", "2026-07")
    rows = obveze.list_period(spine, "PDV", "2026-07")
    assert [r["client"] for r in rows] == ["Alfa", "Zebra"]  # Beta van, abecedno


def test_mark_sent_moves_down(spine):
    _seed(spine)
    obveze.ensure_period(spine, "PDV", "2026-07")
    rows = obveze.list_period(spine, "PDV", "2026-07")
    obveze.mark_sent(spine, rows[0]["obligation_id"], "ana")
    rows2 = obveze.list_period(spine, "PDV", "2026-07")
    assert rows2[-1]["client"] == "Alfa" and rows2[-1]["sent"]


def test_ensure_period_idempotent(spine):
    _seed(spine)
    obveze.ensure_period(spine, "PDV", "2026-07")
    obveze.ensure_period(spine, "PDV", "2026-07")
    rows = obveze.list_period(spine, "PDV", "2026-07")
    assert len(rows) == 2


def test_mark_sent_unknown_obligation_raises(spine):
    import pytest
    with pytest.raises(ValueError):
        obveze.mark_sent(spine, 999, "ana")


def _client(spine, cfg):
    return TestClient(create_app(spine, cfg))


def _token(c, spine):
    add_user(spine, "ana", "tajna")
    return c.post("/auth/login", json={"username": "ana", "password": "tajna"}).json()["token"]


def test_api_obveze_html_escapes_client_name(spine, cfg):
    with spine.write() as conn:
        conn.execute(
            "INSERT INTO clients(name, oib, pdv_status) VALUES ('<script>Zloća</script>', '9', 'u sustavu pdv')"
        )
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/obveze?kind=PDV&period=2026-07", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<script>Zloća</script>" not in r.text
    assert "&lt;script&gt;Zloća&lt;/script&gt;" in r.text


def test_login_sets_cookie(spine, cfg):
    c = _client(spine, cfg)
    add_user(spine, "ana", "tajna")
    r = c.post("/auth/login", json={"username": "ana", "password": "tajna"})
    assert r.status_code == 200
    assert "ragspine_token" in r.cookies


def test_obveze_via_cookie_only(spine, cfg):
    _seed(spine)
    c = _client(spine, cfg)
    add_user(spine, "ana", "tajna")
    c.post("/auth/login", json={"username": "ana", "password": "tajna"})
    assert "ragspine_token" in c.cookies  # persisted on the client's cookie jar
    r = c.get("/obveze?kind=PDV&period=2026-07")  # no Authorization header
    assert r.status_code == 200
    assert "Alfa" in r.text


def test_obveze_no_auth_redirects_to_login(spine, cfg):
    c = _client(spine, cfg)
    r = c.get("/obveze", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_obveze_invalid_kind_400(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/obveze?kind=BOGUS", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 400


def test_obveze_mark_unknown_obligation_404(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.post("/obveze/mark", json={"obligation_id": 999},
                headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 404


# ---------- registar vrsta obveza (data-driven) ----------

def test_types_seeded_by_default(spine):
    kinds = {t["kind"] for t in obveze.list_types(spine)}
    assert {"PDV", "JOPPD", "DOH"} <= kinds
    pdv = obveze.get_type(spine, "PDV")
    assert pdv["applies_to"] == "pdv" and pdv["active"] == 1


def test_pdv_excludes_canonical_negative_status(spine):
    # "nije u sustavu PDV-a" sadrži "u sustavu" — ne smije dobiti PDV obvezu
    with spine.write() as c:
        c.execute("INSERT INTO clients(name,oib,pdv_status,active) VALUES('Da','1','u sustavu PDV-a',1)")
        c.execute("INSERT INTO clients(name,oib,pdv_status,active) VALUES('Ne','2','nije u sustavu PDV-a',1)")
    obveze.ensure_period(spine, "PDV", "2026-08")
    assert [r["client"] for r in obveze.list_period(spine, "PDV", "2026-08")] == ["Da"]


def test_ensure_period_reconciles_stale_unsent(spine):
    with spine.write() as c:
        cid = c.execute("INSERT INTO clients(name,oib,active,has_employees) VALUES('E','1',1,1)").lastrowid
    obveze.ensure_period(spine, "JOPPD", "2026-08")
    assert [r["client"] for r in obveze.list_period(spine, "JOPPD", "2026-08")] == ["E"]
    # klijent izgubi zaposlene -> neposlana JOPPD obveza se ukloni pri sljedećem ensure
    with spine.write() as c:
        c.execute("UPDATE clients SET has_employees=0 WHERE id=?", (cid,))
    obveze.ensure_period(spine, "JOPPD", "2026-08")
    assert obveze.list_period(spine, "JOPPD", "2026-08") == []


def test_ensure_period_keeps_sent_obligation_as_history(spine):
    with spine.write() as c:
        cid = c.execute("INSERT INTO clients(name,oib,active,has_employees) VALUES('E','1',1,1)").lastrowid
    obveze.ensure_period(spine, "JOPPD", "2026-08")
    oid = obveze.list_period(spine, "JOPPD", "2026-08")[0]["obligation_id"]
    obveze.mark_sent(spine, oid, "ana")            # predano
    with spine.write() as c:
        c.execute("UPDATE clients SET has_employees=0 WHERE id=?", (cid,))
    obveze.ensure_period(spine, "JOPPD", "2026-08")  # ne smije obrisati poslano
    rows = obveze.list_period(spine, "JOPPD", "2026-08")
    assert len(rows) == 1 and rows[0]["sent"] == 1


def test_upsert_rule_must_match_frequency(spine):
    import pytest
    with pytest.raises(ValueError):
        obveze.upsert_type(spine, "X", "X", "yearly:04-30", "monthly", "all_active")
    with pytest.raises(ValueError):
        obveze.upsert_type(spine, "X", "X", "monthly:15", "yearly", "all_active")
    # ispravno + prazno pravilo su OK
    obveze.upsert_type(spine, "OK1", "OK", "monthly:15", "monthly", "all_active")
    obveze.upsert_type(spine, "OK2", "OK", "", "yearly", "all_active")


def test_joppd_gates_on_employees(spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name,oib,pdv_status,active,has_employees) VALUES('Emp','1','u sustavu pdv',1,1)")
        c.execute("INSERT INTO clients(name,oib,pdv_status,active,has_employees) VALUES('NoEmp','2','u sustavu pdv',1,0)")
    obveze.ensure_period(spine, "JOPPD", "2026-08")
    rows = obveze.list_period(spine, "JOPPD", "2026-08")
    assert [r["client"] for r in rows] == ["Emp"]


def test_pdv_quarterly_client_skips_non_quarter_month(spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name,oib,pdv_status,active,pdv_freq) VALUES('Mje','1','u sustavu pdv',1,'monthly')")
        c.execute("INSERT INTO clients(name,oib,pdv_status,active,pdv_freq) VALUES('Kvar','2','u sustavu pdv',1,'quarterly')")
    obveze.ensure_period(spine, "PDV", "2026-05")   # svibanj — nije kvartalni mjesec
    assert [r["client"] for r in obveze.list_period(spine, "PDV", "2026-05")] == ["Mje"]
    obveze.ensure_period(spine, "PDV", "2026-04")   # travanj — kvartalni mjesec
    assert {r["client"] for r in obveze.list_period(spine, "PDV", "2026-04")} == {"Mje", "Kvar"}


def test_doh_default_is_yearly_dohodak(spine):
    doh = obveze.get_type(spine, "DOH")
    assert doh["frequency"] == "yearly" and doh["applies_to"] == "dohodak"
    assert doh["active"] == 0  # nije tab dok ga radnik ne uključi
    posd = obveze.get_type(spine, "PO-SD")
    assert posd["applies_to"] == "pausal" and posd["frequency"] == "yearly"


def test_regime_yearly_obligation_only_in_filing_month(spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name,oib,active,regime) VALUES('Doh','1',1,'dohodak')")
        c.execute("INSERT INTO clients(name,oib,active,regime) VALUES('Pau','2',1,'pausal')")
    # DOH -> yearly:02-28 -> samo u veljači
    obveze.ensure_period(spine, "DOH", "2026-03")
    assert obveze.list_period(spine, "DOH", "2026-03") == []
    obveze.ensure_period(spine, "DOH", "2026-02")
    assert [r["client"] for r in obveze.list_period(spine, "DOH", "2026-02")] == ["Doh"]
    # dohodaš ne dobiva paušalni PO-SD
    obveze.ensure_period(spine, "PO-SD", "2026-01")
    assert [r["client"] for r in obveze.list_period(spine, "PO-SD", "2026-01")] == ["Pau"]


def test_manual_type_only_assigned_clients(spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name,oib,active) VALUES('Ima','1',1)")
        c.execute("INSERT INTO clients(name,oib,active) VALUES('Nema','2',1)")
    obveze.upsert_type(spine, "najam", "Najamnina", "monthly:15", "monthly", "manual")
    assert obveze.get_type(spine, "NAJAM")["applies_to"] == "manual"  # normalizirano na velika
    obveze.ensure_period(spine, "NAJAM", "2026-08")
    assert obveze.list_period(spine, "NAJAM", "2026-08") == []  # nitko nije dodijeljen
    ima = spine.read().execute("SELECT id FROM clients WHERE name='Ima'").fetchone()["id"]
    obveze.set_client_types(spine, ima, ["NAJAM"])
    obveze.ensure_period(spine, "NAJAM", "2026-08")
    assert [r["client"] for r in obveze.list_period(spine, "NAJAM", "2026-08")] == ["Ima"]


def test_upsert_type_validates(spine):
    import pytest
    with pytest.raises(ValueError):
        obveze.upsert_type(spine, "X", "X", "", "weekly", "pdv")      # bad frequency
    with pytest.raises(ValueError):
        obveze.upsert_type(spine, "X", "X", "", "monthly", "nonsense")  # bad applies_to
    with pytest.raises(ValueError):
        obveze.upsert_type(spine, "  ", "X", "", "monthly", "pdv")     # empty kind


def test_types_endpoints_and_new_tab(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    H = {"Authorization": f"Bearer {tok}"}
    # GET registry
    assert any(t["kind"] == "PDV" for t in c.get("/obveze/tipovi", headers=H).json())
    # POST new manual type
    r = c.post("/obveze/tipovi", json={"kind": "najam", "label": "Najamnina",
               "rule": "monthly:15", "frequency": "monthly", "applies_to": "manual"}, headers=H)
    assert r.status_code == 200 and r.json()["kind"] == "NAJAM"
    # appears as a tab on the obveze page
    page = c.get("/obveze?kind=PDV&period=2026-08", headers=H).text
    assert "NAJAM</a>" in page or "Najamnina</a>" in page


def test_client_obligations_settings_roundtrip(spine, cfg):
    with spine.write() as conn:
        cid = conn.execute("INSERT INTO clients(name,oib,active) VALUES('Klk','9',1)").lastrowid
    obveze.upsert_type(spine, "NAJAM", "Najamnina", "monthly:15", "monthly", "manual")
    c = _client(spine, cfg)
    tok = _token(c, spine)
    H = {"Authorization": f"Bearer {tok}"}
    r = c.post(f"/clients/{cid}/obveze-postavke",
               json={"has_employees": 1, "pdv_freq": "quarterly", "manual_kinds": ["NAJAM"]}, headers=H)
    assert r.status_code == 200
    got = c.get(f"/clients/{cid}/obveze-postavke", headers=H).json()
    assert got["has_employees"] == 1 and got["pdv_freq"] == "quarterly"
    assert got["manual_kinds"] == ["NAJAM"]
    # partial POST (only regime) must PRESERVE the other three settings
    c.post(f"/clients/{cid}/obveze-postavke", json={"regime": "dohodak"}, headers=H)
    got = c.get(f"/clients/{cid}/obveze-postavke", headers=H).json()
    assert got["regime"] == "dohodak"
    assert got["has_employees"] == 1 and got["pdv_freq"] == "quarterly"
    assert got["manual_kinds"] == ["NAJAM"]
    assert c.post(f"/clients/{cid}/obveze-postavke", json={"regime": "xyz"},
                  headers=H).status_code == 400
    assert any(t["kind"] == "NAJAM" for t in got["available_manual"])


def test_client_obligations_bad_pdv_freq_400(spine, cfg):
    with spine.write() as conn:
        cid = conn.execute("INSERT INTO clients(name,oib,active) VALUES('Klk','9',1)").lastrowid
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.post(f"/clients/{cid}/obveze-postavke", json={"pdv_freq": "weekly"},
               headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 400


def test_ui_obveze_tipovi_page(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/ui/obveze-tipovi", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert "Vrste obveza" in r.text
    assert "/obveze/tipovi" in r.text
    assert "@font-face" in r.text
    assert "innerHTML" not in r.text


def test_obveze_page_all_types_inactive_empty_state(spine, cfg):
    c = _client(spine, cfg)
    tok = _token(c, spine)
    H = {"Authorization": f"Bearer {tok}"}
    for k in ("PDV", "JOPPD"):  # deaktiviraj sve default-aktivne
        t = obveze.get_type(spine, k)
        obveze.upsert_type(spine, k, t["label"], t["rule"], t["frequency"],
                           t["applies_to"], active=False, sort=t["sort"])
    r = c.get("/obveze", headers=H)
    assert r.status_code == 200  # ne 400
    assert "Nijedna vrsta obveze" in r.text
    assert "/ui/obveze-tipovi" in r.text


def test_ui_obveze_tipovi_no_auth_redirects(spine, cfg):
    c = _client(spine, cfg)
    r = c.get("/ui/obveze-tipovi", follow_redirects=False)
    assert r.status_code == 303


def test_obveze_page_has_type_tabs_and_two_sections(spine, cfg):
    _seed(spine)
    c = _client(spine, cfg)
    tok = _token(c, spine)
    r = c.get("/obveze?kind=PDV&period=2026-08", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    text = r.text
    # type selector tabs (PDV + JOPPD), PDV active
    assert 'class="obveze-tab active"' in text and "PDV</a>" in text
    assert "JOPPD</a>" in text
    # two sections: unsent above, predano below; checkbox marks + drops down
    assert "Za predati" in text and "Predano" in text
    assert "onToggle(this)" in text
    assert 'id="list-unsent"' in text and 'id="list-sent"' in text
    # month navigation
    assert "Kolovoz 2026." in text
    assert "period=2026-07" in text and "period=2026-09" in text


def test_obveze_page_sent_client_starts_in_predano(spine, cfg):
    _seed(spine)
    obveze.ensure_period(spine, "PDV", "2026-08")
    rows = obveze.list_period(spine, "PDV", "2026-08")
    obveze.mark_sent(spine, rows[0]["obligation_id"], "ana")  # Alfa -> predano

    c = _client(spine, cfg)
    tok = _token(c, spine)
    text = c.get("/obveze?kind=PDV&period=2026-08",
                 headers={"Authorization": f"Bearer {tok}"}).text
    # the sent row renders checked (lives in the Predano section)
    sent_block = text.split('id="list-sent"')[1]
    assert "Alfa" in sent_block and "checked" in sent_block


def test_obveze_empty_state_hidden_when_section_has_rows(spine, cfg):
    _seed(spine)  # Alfa + Zebra are PDV obligors -> unsent section non-empty
    c = _client(spine, cfg)
    tok = _token(c, spine)
    text = c.get("/obveze?kind=PDV&period=2026-08",
                 headers={"Authorization": f"Bearer {tok}"}).text
    # the "Za predati" empty-state paragraph must be hidden while rows exist
    empty_p = [ln for ln in text.splitlines() if 'id="empty-unsent"' in ln][0]
    assert "display:none" in empty_p
