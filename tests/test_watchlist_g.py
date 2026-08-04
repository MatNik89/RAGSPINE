"""Piece G: vlastite ključne riječi + keyword_hit obavijest + Excel izvoz + UI."""
import pytest

from ragspine.core import optional
from ragspine.web import watchlist

openpyxl = optional.need("openpyxl", "test xlsx")


def test_keywords_roundtrip_dedup_and_validation(spine):
    assert watchlist.get_keywords(spine) == []
    out = watchlist.set_keywords(spine, ["Paušalni obrt", "  pausalni OBRT ", "PDV", ""])
    assert out == ["Paušalni obrt", "PDV"]  # dedup preko folda dijakritika
    assert watchlist.get_keywords(spine) == ["Paušalni obrt", "PDV"]
    with pytest.raises(ValueError):
        watchlist.set_keywords(spine, [7])


def test_match_keywords_diacritics_fold(spine):
    watchlist.set_keywords(spine, ["turistička pristojba"])
    assert watchlist.match_keywords(spine, "nova TURISTICKA pristojba za apartmane") \
        == ["turistička pristojba"]
    assert watchlist.match_keywords(spine, "ništa od toga") == []


def test_keyword_hit_notification_on_change(spine, cfg):
    watchlist.set_keywords(spine, ["paušal"])
    sid = watchlist.add_source(spine, "http://primjer.hr/zakon")
    row = watchlist.get_source(spine, sid)
    calls = {"n": 0}

    def fake_fetch(url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return b"<html>Stari tekst zakona o obrtu bez ikakvih pragova.</html>"
        return b"<html>Novi tekst: mijenja se pausal i prag prihoda za obrtnike.</html>"

    assert watchlist.check_source(spine, cfg, row, fetch=fake_fetch) is None  # baseline
    ch = watchlist.check_source(spine, cfg, row, fetch=fake_fetch)
    assert ch is not None
    hits = spine.read().execute(
        "SELECT body FROM notifications WHERE kind='keyword_hit'").fetchall()
    assert len(hits) == 1 and "paušal" in hits[0]["body"]


@pytest.mark.skipif(openpyxl is None, reason="openpyxl not installed")
def test_export_xlsx_roundtrip(spine, cfg):
    import io
    sid = watchlist.add_source(spine, "http://primjer.hr/zakon", category="PDV")
    with spine.write() as c:
        c.execute("INSERT INTO upcoming_changes(source_id,description,effective_date) "
                  "VALUES(?,?,?)", (sid, "Nova stopa stupa na snagu", "2026-09-01"))
    data = watchlist.export_xlsx(spine)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    assert set(wb.sheetnames) == {"Nadolazeće promjene", "Rokovi", "Izvori"}
    ws = wb["Nadolazeće promjene"]
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0] == ("Stupa na snagu", "Opis", "Izvor")
    assert rows[1][0] == "2026-09-01" and "Nova stopa" in rows[1][1]


def test_api_keywords_upcoming_toggle_page(spine, cfg):
    from fastapi.testclient import TestClient
    from ragspine.web.api import create_app
    from ragspine.web.deps import add_user

    c = TestClient(create_app(spine, cfg))
    assert c.get("/watchlist/keywords").status_code in (401, 403)
    add_user(spine, "ana", "pw")
    tok = c.post("/auth/login", json={"username": "ana", "password": "pw"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}

    r = c.post("/watchlist/keywords", headers=h, json={"keywords": ["PDV", "paušal"]})
    assert r.status_code == 200
    assert c.get("/watchlist/keywords", headers=h).json() == ["PDV", "paušal"]

    sid = c.post("/watchlist/sources", headers=h,
                 json={"url": "http://primjer.hr/x"}).json()["id"]
    assert c.post(f"/watchlist/sources/{sid}/toggle", headers=h).json()["active"] == 0
    assert c.post("/watchlist/sources/424242/toggle", headers=h).status_code == 404

    assert c.get("/watchlist/upcoming", headers=h).json() == []
    r = c.get("/ui/pracenje", headers=h)
    assert r.status_code == 200 and "Praćenje propisa" in r.text
    if openpyxl is not None:
        r = c.get("/watchlist/export.xlsx", headers=h)
        assert r.status_code == 200
        assert "attachment" in r.headers["content-disposition"]


def test_keywords_cap_and_nfd_matching(spine):
    with pytest.raises(ValueError, match="max 100"):
        watchlist.set_keywords(spine, [f"kw{i}" for i in range(150)])
    # dekomponirani Unicode (c + combining caron) matcha kao č
    watchlist.set_keywords(spine, ["račun"])
    nfd = "izdan je nov račun za klijenta"
    assert watchlist.match_keywords(spine, nfd) == ["račun"]


@pytest.mark.skipif(openpyxl is None, reason="openpyxl not installed")
def test_export_xlsx_neutralizes_formulas(spine, cfg):
    import io
    sid = watchlist.add_source(spine, "http://primjer.hr/x", category="=1+1")
    with spine.write() as c:
        c.execute("INSERT INTO upcoming_changes(source_id,description,effective_date) "
                  "VALUES(?,?,?)", (sid, '=HYPERLINK("http://zlo.hr","klik")', "2026-09-01"))
    wb = openpyxl.load_workbook(io.BytesIO(watchlist.export_xlsx(spine)))
    cell = wb["Nadolazeće promjene"].cell(2, 2)
    assert cell.data_type != "f" and cell.value.startswith("'=")
