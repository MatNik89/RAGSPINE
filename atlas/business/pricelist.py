# Service price list — the accounting FIRM's own price list for billing its
# clients (not client tax calculations). Per-client monthly quote + a simple
# market-position comparison against the firm's own client base.
#
# ponytail: DEFAULT_CJENIK amounts are plausible illustrative EUR prices for
# a small Croatian accounting office, not a market survey. Operator adjusts via
# direct UPDATE on the cjenik table (no override layer — this is the firm's
# own price, not a legal figure to track for drift).

from decimal import ROUND_HALF_UP, Decimal

DEFAULT_CJENIK: list[dict] = [
    {"key": "mjesecno_knjigovodstvo", "usluga": "Mjesečno knjigovodstvo",
     "cijena": 150.0, "unit": "EUR/mj"},
    {"key": "obracun_place", "usluga": "Obračun plaće po zaposleniku",
     "cijena": 15.0, "unit": "EUR/zaposleniku"},
    {"key": "pdv_prijava", "usluga": "PDV prijava", "cijena": 40.0, "unit": "EUR/mj"},
    {"key": "joppd_obrazac", "usluga": "JOPPD obrazac", "cijena": 10.0, "unit": "EUR/obrazac"},
    {"key": "godisnji_izvjestaji", "usluga": "Godišnji financijski izvještaji",
     "cijena": 300.0, "unit": "EUR/god"},
    {"key": "porezno_savjetovanje", "usluga": "Porezno savjetovanje",
     "cijena": 60.0, "unit": "EUR/h"},
    {"key": "osnivanje_tvrtke", "usluga": "Osnivanje tvrtke",
     "cijena": 400.0, "unit": "EUR/jednokratno"},
    {"key": "zatvaranje_likvidacija", "usluga": "Zatvaranje/likvidacija",
     "cijena": 350.0, "unit": "EUR/jednokratno"},
    {"key": "izrada_fin_izvjestaja", "usluga": "Izrada financijskih izvještaja",
     "cijena": 80.0, "unit": "EUR/kom"},
]

_2DP = Decimal("0.01")


def _d(x) -> Decimal:
    return Decimal(str(x)).quantize(_2DP, rounding=ROUND_HALF_UP)


def seed(spine) -> int:
    n = 0
    with spine.write() as c:
        for item in DEFAULT_CJENIK:
            cur = c.execute(
                "INSERT OR IGNORE INTO cjenik(key,usluga,cijena,valuta,unit) VALUES(?,?,?,?,?)",
                (item["key"], item["usluga"], item["cijena"], "EUR", item["unit"]),
            )
            n += cur.rowcount
    return n


def price_list(spine) -> list[dict]:
    rows = spine.read().execute(
        "SELECT id,key,usluga,cijena,valuta,unit FROM cjenik ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def _lookup(spine, key: str):
    return spine.read().execute(
        "SELECT usluga,cijena FROM cjenik WHERE key=? OR usluga=? LIMIT 1", (key, key)
    ).fetchone()


def get_price(spine, key: str, default: float = 0.0) -> float:
    row = _lookup(spine, key)
    return row["cijena"] if row else default


def calculate_price(spine, client_id: int, employees: int = 0, extras: list[str] | None = None) -> dict:
    client = spine.read().execute(
        "SELECT name,pausal_eur,pdv_status FROM clients WHERE id=?", (client_id,)
    ).fetchone()
    if client is None:
        raise ValueError("nepoznat klijent")

    items = []

    base = client["pausal_eur"] or 0
    if not base:
        base = get_price(spine, "mjesecno_knjigovodstvo")
    items.append({"naziv": "Mjesečno knjigovodstvo", "iznos": _d(base)})

    if employees > 0:
        per_emp = get_price(spine, "obracun_place")
        items.append({"naziv": f"Obračun plaće ({employees} zaposlenika)",
                        "iznos": _d(per_emp * employees)})
        joppd = get_price(spine, "joppd_obrazac")
        items.append({"naziv": "JOPPD obrazac", "iznos": _d(joppd)})

    pdv_status = (client["pdv_status"] or "").lower()
    if "u sustavu" in pdv_status:
        pdv = get_price(spine, "pdv_prijava")
        items.append({"naziv": "PDV prijava", "iznos": _d(pdv)})

    for ekey in (extras or []):
        row = _lookup(spine, ekey)
        name = row["usluga"] if row else ekey
        amount = row["cijena"] if row else 0.0
        items.append({"naziv": name, "iznos": _d(amount)})

    total = sum((s["iznos"] for s in items), Decimal("0.00"))

    return {"stavke": items, "ukupno": total, "klijent": client["name"]}


def compare_to_market(spine, client_id: int) -> dict:
    client = spine.read().execute(
        "SELECT name,pausal_eur FROM clients WHERE id=?", (client_id,)
    ).fetchone()
    if client is None:
        raise ValueError("nepoznat klijent")

    client_pausal = client["pausal_eur"] or 0
    others = spine.read().execute(
        "SELECT pausal_eur FROM clients WHERE id!=? AND active=1 AND pausal_eur>0",
        (client_id,),
    ).fetchall()

    if not others:
        return {
            "klijent_pausal": _d(client_pausal),
            "prosjek_trzista": None,
            "preporuka": "Nema dovoljno podataka o drugim klijentima za usporedbu.",
        }

    average = sum(r["pausal_eur"] for r in others) / len(others)

    if client_pausal <= 0 or average == 0:
        recommendation = "Nema dovoljno podataka o drugim klijentima za usporedbu."
    else:
        diff = (client_pausal - average) / average
        if diff < -0.15:
            recommendation = "Ispod tržišta — razmisli o povećanju naknade"
        elif diff > 0.15:
            recommendation = "Iznad tržišta — u redu ako je opravdano"
        else:
            recommendation = "U skladu s tržištem"

    return {
        "klijent_pausal": _d(client_pausal),
        "prosjek_trzista": _d(average),
        "preporuka": recommendation,
    }
