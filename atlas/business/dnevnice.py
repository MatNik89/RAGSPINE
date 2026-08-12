# Per-diem allowances (dnevnice) for official travel abroad - 34 countries.
#
# ponytail: RATES are approximate default amounts (EUR), not the official NN table.
# The operator maintains the real amounts via spine.set_override("dnevnica", country, iznos)
# or learn/watchlist follows the Decree and proposes an override. Upgrade path: seed RATES
# from an official source + a watchlist for changes.

SOURCE = "Odluka o visini dnevnice za službena putovanja u inozemstvo, NN"

RATES: dict[str, float] = {
    "Hrvatska": 30.0,
    # EU27
    "Austrija": 70.0,
    "Belgija": 75.0,
    "Bugarska": 50.0,
    "Cipar": 60.0,
    "Češka": 55.0,
    "Danska": 80.0,
    "Estonija": 55.0,
    "Finska": 75.0,
    "Francuska": 70.0,
    "Grčka": 60.0,
    "Irska": 75.0,
    "Italija": 70.0,
    "Latvija": 55.0,
    "Litva": 55.0,
    "Luksemburg": 75.0,
    "Mađarska": 55.0,
    "Malta": 65.0,
    "Nizozemska": 75.0,
    "Njemačka": 70.0,
    "Poljska": 55.0,
    "Portugal": 65.0,
    "Rumunjska": 50.0,
    "Slovačka": 55.0,
    "Slovenija": 60.0,
    "Španjolska": 65.0,
    "Švedska": 80.0,
    # other
    "UK": 75.0,
    "Švicarska": 100.0,
    "Norveška": 90.0,
    "Srbija": 45.0,
    "BiH": 45.0,
    "SAD": 100.0,
    "Sjeverna Makedonija": 45.0,
}


def obracun(country: str, full_days: int, half_days: int = 0,
            smjestaj: float = 0, prijevoz: float = 0, spine=None) -> dict:
    override = spine.get_override("dnevnica", country) if spine is not None else None
    if override is not None:
        iznos = float(str(override).replace(",", "."))
    elif country in RATES:
        iznos = RATES[country]
    else:
        raise ValueError(f"Nepoznata država za dnevnicu: {country!r}")

    ukupno_dnevnice = round(full_days * iznos + half_days * iznos * 0.5, 2)
    ukupno = round(ukupno_dnevnice + smjestaj + prijevoz, 2)

    return {
        "country": country,
        "dnevnica_iznos": round(iznos, 2),
        "ukupno_dnevnice": ukupno_dnevnice,
        "ukupno": ukupno,
    }
