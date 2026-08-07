from datetime import date

from ragspine.business import obveze, rokovi


def test_holidays_fixed_and_movable_2026():
    hs = rokovi.holidays(2026)
    assert date(2026, 1, 1) in hs        # Nova godina
    assert date(2026, 8, 15) in hs       # Velika Gospa
    assert date(2026, 4, 6) in hs        # Uskrsni ponedjeljak (Uskrs 2026 = 5.4.)
    assert date(2026, 6, 4) in hs        # Tijelovo (Uskrs + 60)


def test_next_workday_skips_weekend_and_holiday():
    # 15.8.2026 = subota I Velika Gospa -> pomak na ponedjeljak 17.8.
    assert rokovi.next_workday(date(2026, 8, 15)) == date(2026, 8, 17)
    # običan radni dan ostaje isti
    assert rokovi.next_workday(date(2026, 8, 20)) == date(2026, 8, 20)


def test_due_for_month_rules():
    assert rokovi.due_for_month("monthly:20", 2026, 8) == date(2026, 8, 20)
    assert rokovi.due_for_month("quarterly:20", 2026, 8) is None   # kolovoz nije kvartalni
    assert rokovi.due_for_month("quarterly:20", 2026, 7) == date(2026, 7, 20)
    assert rokovi.due_for_month("yearly:02-28", 2026, 2) == date(2026, 2, 28)
    assert rokovi.due_for_month("yearly:02-28", 2026, 3) is None
    # dan preko kraja mjeseca se skraćuje
    assert rokovi.due_for_month("monthly:31", 2026, 2) == date(2026, 2, 28)


def test_generate_materialises_and_shifts(spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name,oib,pdv_status,active) VALUES('A','1','u sustavu pdv',1)")
    obveze.upsert_type(spine, "TZ", "Turistička", "monthly:15", "monthly", "manual")

    added = rokovi.generate(spine, months_ahead=2, today=date(2026, 8, 2))
    assert added > 0
    dates = {(r["kind"], r["due"]) for r in
             spine.read().execute("SELECT kind, due FROM deadline_dates").fetchall()}
    # PDV od 2026.: zadnji dan mjeseca — 31.8. je ponedjeljak -> ostaje
    assert ("PDV", "2026-08-31") in dates
    # TZ 15.8. = subota+blagdan -> pomaknuto na 17.8.
    assert ("TZ", "2026-08-17") in dates
    assert ("TZ", "2026-08-15") not in dates


def test_generate_idempotent(spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name,oib,pdv_status,active) VALUES('A','1','u sustavu pdv',1)")
    rokovi.generate(spine, months_ahead=3, today=date(2026, 8, 2))
    before = spine.read().execute("SELECT COUNT(*) n FROM deadline_dates").fetchone()["n"]
    # reconcile-then-regenerate: broj redova mora ostati stabilan (bez gomilanja)
    rokovi.generate(spine, months_ahead=3, today=date(2026, 8, 2))
    after = spine.read().execute("SELECT COUNT(*) n FROM deadline_dates").fetchone()["n"]
    assert before == after and before > 0


def test_generate_reconciles_legacy_unshifted_date(spine):
    # legacy seed upiše NEPOMAKNUTI datum na blagdan; generate ga zamijeni pomaknutim
    with spine.write() as c:
        c.execute("INSERT INTO clients(name,oib,pdv_status,active) VALUES('A','1','u sustavu pdv',1)")
    obveze.upsert_type(spine, "TZ", "Turistička", "monthly:15", "monthly", "manual")
    with spine.write() as c:
        c.execute("INSERT INTO deadline_dates(kind,due,year) VALUES('TZ','2026-08-15',2026)")  # subota+blagdan
    rokovi.generate(spine, months_ahead=0, today=date(2026, 8, 2))
    dues = [r["due"] for r in spine.read().execute(
        "SELECT due FROM deadline_dates WHERE kind='TZ' ORDER BY due").fetchall()]
    assert dues == ["2026-08-17"]  # pomaknuto; nema duplog nepomaknutog 15.8.


def test_rokovi_job_registered(spine, cfg):
    from ragspine.ops import jobs
    from ragspine.ops.scheduler import Scheduler
    sched = Scheduler(spine, cfg)
    jobs.register_defaults(sched)
    assert "rokovi" in {j.name for j in sched.jobs}


def test_pdv_rok_2026_zadnji_dan_mjeseca():
    """Od 1.1.2026. PDV/ZP/PDV-S obrasci se predaju do ZADNJEG dana mjeseca
    (izmjene Zakona o PDV-u), ne do 20. — pravilo monthly:31 se clampa na
    kraj mjeseca (veljača 28., travanj 30.)."""
    assert rokovi.due_for_month("monthly:31", 2026, 2) == date(2026, 2, 28)
    assert rokovi.due_for_month("monthly:31", 2026, 4) == date(2026, 4, 30)
    assert rokovi.due_for_month("monthly:31", 2026, 8) == date(2026, 8, 31)


def test_migracija_pdv_rok_na_kraj_mjeseca(tmp_path):
    """Postojeća baza sa starim default pravilom monthly:20 za PDV/PDV-S/ZP
    dobije monthly:31 pri otvaranju (zakonska promjena 2026)."""
    from ragspine.core.spine import init_spine
    db = str(tmp_path / "t.db")
    s1 = init_spine(db)
    with s1.write() as c:
        c.execute("INSERT OR REPLACE INTO obligation_types(kind,label,rule,frequency,applies_to,sort,active) "
                  "VALUES('PDV','PDV','monthly:20','monthly','pdv',10,1)")
        c.execute("INSERT OR REPLACE INTO deadlines(kind,rule,description) VALUES('PDV','monthly:20','x')")
        c.execute("INSERT OR REPLACE INTO deadlines(kind,rule,description) VALUES('ZP','monthly:20','x')")
    s2 = init_spine(db)
    r = s2.read()
    assert r.execute("SELECT rule FROM obligation_types WHERE kind='PDV'").fetchone()["rule"] == "monthly:31"
    assert r.execute("SELECT rule FROM deadlines WHERE kind='PDV'").fetchone()["rule"] == "monthly:31"
    assert r.execute("SELECT rule FROM deadlines WHERE kind='ZP'").fetchone()["rule"] == "monthly:31"
    # tuđa custom vrijednost se NE dira
    with s2.write() as c:
        c.execute("UPDATE obligation_types SET rule='monthly:25' WHERE kind='PDV'")
    s3 = init_spine(db)
    assert s3.read().execute("SELECT rule FROM obligation_types WHERE kind='PDV'").fetchone()["rule"] == "monthly:25"
