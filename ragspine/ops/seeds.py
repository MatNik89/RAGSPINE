"""DB seed data: kontni plan, watch defaults, dnevnice rates (+ kalendar/quickref via all())."""
from ragspine.business import dnevnice, kalendar, quickref
from ragspine.web.watchlist import DEFAULT_RSS, add_source

# ponytail: plausible RRIF-style konto raspored (razred = prva znamenka), ne
# službena tablica. Operater dopunjuje/ispravlja kroz izravni INSERT ili
# buduću watchlist na RRIF izvor.
KONTNI_PLAN: list[tuple[str, str, str]] = [
    ("0010", "Zemljište", "0"),
    ("0020", "Građevinski objekti", "0"),
    ("0030", "Postrojenja i oprema", "0"),
    ("0040", "Nematerijalna imovina", "0"),
    ("0050", "Ulaganja u nekretnine", "0"),
    ("1000", "Blagajna", "1"),
    ("1010", "Žiro račun", "1"),
    ("1020", "Devizni račun", "1"),
    ("1200", "Kupci u zemlji", "1"),
    ("1210", "Kupci u inozemstvu", "1"),
    ("1300", "Potraživanja za PDV pretporez", "1"),
    ("1400", "Zalihe sirovina i materijala", "1"),
    ("1420", "Zalihe gotovih proizvoda", "1"),
    ("2200", "Dobavljači u zemlji", "2"),
    ("2210", "Dobavljači u inozemstvu", "2"),
    ("2400", "Obveze za PDV", "2"),
    ("2500", "Obveze za plaće", "2"),
    ("2510", "Obveze za doprinose", "2"),
    ("2600", "Kratkoročni krediti", "2"),
    ("3000", "Usluge telekomunikacija", "3"),
    ("3010", "Najamnina", "3"),
    ("3020", "Energija", "3"),
    ("3030", "Usluge održavanja", "3"),
    ("3040", "Reprezentacija", "3"),
    ("4000", "Materijal", "4"),
    ("4010", "Sirovine", "4"),
    ("4020", "Sitni inventar", "4"),
    ("4030", "Rezervni dijelovi", "4"),
    ("4040", "Ambalaža", "4"),
    ("5000", "Plaće zaposlenika", "5"),
    ("5010", "Doprinosi na plaće", "5"),
    ("5020", "Naknade troškova (dnevnice, kilometraža)", "5"),
    ("5030", "Otpremnine", "5"),
    ("6000", "Kamate na kredite", "6"),
    ("6010", "Tečajne razlike", "6"),
    ("6020", "Bankovne naknade", "6"),
    ("7000", "Prihodi od prodaje proizvoda", "7"),
    ("7010", "Prihodi od prodaje usluga", "7"),
    ("7500", "Ostali poslovni prihodi", "7"),
    ("7510", "Prihodi od najma", "7"),
    ("7520", "Prihodi od kamata", "7"),
    ("8000", "Izvanredni prihodi", "8"),
    ("8010", "Izvanredni rashodi", "8"),
    ("9000", "Temeljni (upisani) kapital", "9"),
    ("9010", "Zakonske rezerve", "9"),
    ("9020", "Zadržana dobit", "9"),
    ("9030", "Gubitak razdoblja", "9"),
]

# ponytail: URL unverified against live porezna-uprava.gov.hr (no network
# access at write time), same caveat as web/watchlist.py DEFAULT_RSS tip=3.
# Operater ispravlja preko spine.set_override ako se promijeni.
TAX_CALENDAR_URL = "https://www.porezna-uprava.gov.hr/kalendar-poreznih-obveza"


def kontni_plan(spine) -> int:
    n = 0
    with spine.write() as c:
        for konto, naziv, razred in KONTNI_PLAN:
            cur = c.execute(
                "INSERT OR IGNORE INTO kontni_plan(konto,naziv,razred) VALUES(?,?,?)",
                (konto, naziv, razred),
            )
            n += cur.rowcount
    return n


def watch_defaults(spine) -> int:
    n = 0
    sources = [(TAX_CALENDAR_URL, "kalendar", "page")]
    sources += [(url, category, "rss") for url, category in DEFAULT_RSS]
    for url, category, kind in sources:
        existing = spine.read().execute(
            "SELECT id FROM watch_sources WHERE url=?", (url,)
        ).fetchone()
        add_source(spine, url, category=category, kind=kind)
        if existing is None:
            n += 1
    return n


def dnevnice_seed(spine) -> int:
    n = 0
    with spine.write() as c:
        for country, amount in dnevnice.RATES.items():
            cur = c.execute(
                "INSERT OR IGNORE INTO dnevnice_rates(country,amount,currency,source) VALUES(?,?,?,?)",
                (country, amount, "EUR", dnevnice.SOURCE),
            )
            n += cur.rowcount
    return n


def all(spine, year: int) -> dict:
    return {
        "kontni_plan": kontni_plan(spine),
        "watch": watch_defaults(spine),
        "quickref": quickref.seed(spine),
        "kalendar": kalendar.seed(spine, year),
        "dnevnice": dnevnice_seed(spine),
    }
