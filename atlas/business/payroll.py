# Gross -> net salary calculator (HR 2026).
#
# ponytail: no reduction of MIO contributions for low salaries (monthly
# threshold) and no non-taxable receipts (bonuses, meals, transport...).
# Upgrade path: add new override keys (e.g. "mio_reduction",
# "neoporezivo.{vrsta}") when full precision is needed.

OSNOVNI_ODBITAK = 600.0
DIJETE_FAKTORI = [0.5, 0.7, 1.0]  # 1st, 2nd, 3rd child; 4th+ not modeled
INVALIDNOST_FAKTOR = 0.3
PRAG_OSNOVICE = 5000.0
STOPA_NIZA_DEFAULT = 20.0
STOPA_VISA_DEFAULT = 30.0


def _rate(spine, key: str, default: float) -> float:
    """Read a percentage override, tolerating '12%', ' 12 ', '21,5', or missing/garbage."""
    raw = spine.get_override("kalkulator", key, None)
    if raw is None:
        return default
    try:
        return float(str(raw).strip().rstrip("%").replace(",", "."))
    except ValueError:
        return default


def gross_to_net(gross: float, city: str = "", children: int = 0,
                   disability: bool = False, spine=None) -> dict:
    if gross is None or gross < 0:
        raise ValueError("gross mora biti >= 0")

    detalji = []

    doprinosi = round(gross * 0.20, 2)
    detalji.append(f"doprinosi 20% (MIO I 15 + MIO II 5) = {doprinosi}")

    dohodak = gross - doprinosi

    odbitak = OSNOVNI_ODBITAK
    for i in range(min(children, len(DIJETE_FAKTORI))):
        odbitak += DIJETE_FAKTORI[i] * OSNOVNI_ODBITAK
    if disability:
        odbitak += INVALIDNOST_FAKTOR * OSNOVNI_ODBITAK
    detalji.append(f"osobni odbitak = {odbitak}")

    osnovica = max(0.0, dohodak - odbitak)

    stopa_niza = STOPA_NIZA_DEFAULT
    stopa_visa = STOPA_VISA_DEFAULT
    if spine is not None:
        stopa_niza = _rate(spine, f"porez_niza.{city}", STOPA_NIZA_DEFAULT)
        stopa_visa = _rate(spine, f"porez_visa.{city}", STOPA_VISA_DEFAULT)

    niza_osnovica = min(osnovica, PRAG_OSNOVICE)
    visa_osnovica = max(0.0, osnovica - PRAG_OSNOVICE)
    porez = niza_osnovica * stopa_niza / 100 + visa_osnovica * stopa_visa / 100
    detalji.append(f"porez: {niza_osnovica}@{stopa_niza}% + {visa_osnovica}@{stopa_visa}%")

    # legacy surtax (abolished in HR 2024, but the override is honored for compatibility)
    if spine is not None:
        prirez = _rate(spine, f"prirez.{city}", None)
        if prirez is not None:
            porez *= 1 + prirez / 100
            detalji.append(f"legacy prirez {prirez}% primijenjen")

    porez = round(porez, 2)
    neto = round(dohodak - porez, 2)

    return {
        "gross": gross,
        "doprinosi": doprinosi,
        "dohodak": round(dohodak, 2),
        "odbitak": round(odbitak, 2),
        "osnovica": round(osnovica, 2),
        "porez": porez,
        "neto": neto,
        "stopa_niza": stopa_niza,
        "stopa_visa": stopa_visa,
        "detalji": detalji,
    }
