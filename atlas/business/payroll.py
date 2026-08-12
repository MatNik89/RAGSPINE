# Gross -> net salary calculator (HR 2026).
#
# ponytail: no reduction of MIO contributions for low salaries (monthly
# threshold) and no non-taxable receipts (bonuses, meals, transport...).
# Upgrade path: add new override keys (e.g. "mio_reduction",
# "non_taxable.{type}") when full precision is needed.

BASIC_DEDUCTION = 600.0
CHILD_FACTORS = [0.5, 0.7, 1.0]  # 1st, 2nd, 3rd child; 4th+ not modeled
DISABILITY_FACTOR = 0.3
BASE_THRESHOLD = 5000.0
LOWER_RATE_DEFAULT = 20.0
UPPER_RATE_DEFAULT = 30.0


def _rate(spine, key: str, default: float) -> float:
    """Read a percentage override, tolerating '12%', ' 12 ', '21,5', or missing/garbage."""
    raw = spine.get_override("kalkulator", key, None)   # override namespace is stored-data -> kept
    if raw is None:
        return default
    try:
        return float(str(raw).strip().rstrip("%").replace(",", "."))
    except ValueError:
        return default


def gross_to_net(gross: float, city: str = "", children: int = 0,
                   disability: bool = False, spine=None) -> dict:
    # response dict keys + detail strings are kept Croatian (data contract / user-facing
    # breakdown); override-key namespaces (porez_niza/porez_visa/prirez) are stored-data -> kept.
    if gross is None or gross < 0:
        raise ValueError("gross mora biti >= 0")

    details = []

    contributions = round(gross * 0.20, 2)
    details.append(f"doprinosi 20% (MIO I 15 + MIO II 5) = {contributions}")

    income = gross - contributions

    deduction = BASIC_DEDUCTION
    for i in range(min(children, len(CHILD_FACTORS))):
        deduction += CHILD_FACTORS[i] * BASIC_DEDUCTION
    if disability:
        deduction += DISABILITY_FACTOR * BASIC_DEDUCTION
    details.append(f"osobni odbitak = {deduction}")

    base = max(0.0, income - deduction)

    lower_rate = LOWER_RATE_DEFAULT
    upper_rate = UPPER_RATE_DEFAULT
    if spine is not None:
        lower_rate = _rate(spine, f"porez_niza.{city}", LOWER_RATE_DEFAULT)
        upper_rate = _rate(spine, f"porez_visa.{city}", UPPER_RATE_DEFAULT)

    lower_base = min(base, BASE_THRESHOLD)
    upper_base = max(0.0, base - BASE_THRESHOLD)
    tax = lower_base * lower_rate / 100 + upper_base * upper_rate / 100
    details.append(f"porez: {lower_base}@{lower_rate}% + {upper_base}@{upper_rate}%")

    # legacy surtax (abolished in HR 2024, but the override is honored for compatibility)
    if spine is not None:
        surtax = _rate(spine, f"prirez.{city}", None)
        if surtax is not None:
            tax *= 1 + surtax / 100
            details.append(f"legacy prirez {surtax}% primijenjen")

    tax = round(tax, 2)
    net = round(income - tax, 2)

    return {
        "gross": gross,
        "doprinosi": contributions,
        "dohodak": round(income, 2),
        "odbitak": round(deduction, 2),
        "osnovica": round(base, 2),
        "porez": tax,
        "neto": net,
        "stopa_niza": lower_rate,
        "stopa_visa": upper_rate,
        "detalji": details,
    }
