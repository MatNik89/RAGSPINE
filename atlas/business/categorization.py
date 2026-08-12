# Expense categorization - regex rules for suggesting the account + tax deductibility.
#
# ponytail: the account numbers/names are illustrative (RRIF-style group 4 layout),
# not an official chart of accounts. The operator should align them with their own chart of accounts
# (kontni_plan table) or override them through learned corrections (feedback_learn).

import re

_DIACRITICS = str.maketrans("čćžšđČĆŽŠĐ", "cczsdCCZSD")


def _norm(s: str) -> str:
    return (s or "").translate(_DIACRITICS).lower()


def _rule(pattern: str, account: str, name: str, deductible: float, note: str) -> dict:
    return {"pattern": re.compile(pattern), "konto": account, "naziv": name,
            "porezno_priznato": deductible, "note": note}


RULES: list[dict] = [
    _rule(r"ured\w* materijal|\bpapir\b|\btoner\b|\btinta\b|kemijska olovka|spajalic",
          "4000", "Uredski materijal", 1.0, "Porezno priznato u cijelosti."),
    _rule(r"\bstruj\w*|energij\w*|\belektric\w*|\bplin\b|racun za vodu|komunalij",
          "4001", "Energija (struja, plin, voda)", 1.0, "Porezno priznato u cijelosti."),
    _rule(r"\btelefon\w*|\binternet\w*|\bmobitel\w*",
          "4002", "Telefon i internet", 1.0, "Porezno priznato u cijelosti."),
    _rule(r"reprezentacij\w*|\brestoran\w*|ugostitelj\w*|posl\w* rucak|kava s (klijent|partner)\w*",
          "4004", "Reprezentacija", 0.5, "Djelomično porezno priznato 50%, provjeri."),
    _rule(r"\bgoriv\w*|\bbenzin\w*|\bdizel\w*|nafta za auto",
          "4005", "Gorivo za vozila", 0.5, "Djelomično porezno priznato 50% ako vozilo služi i u privatne svrhe, provjeri."),
    _rule(r"bankovn\w*|naknad\w* banke|kartic\w* naknad\w*|provizij\w* banke",
          "4006", "Bankovne naknade", 1.0, "Porezno priznato u cijelosti."),
    _rule(r"odvjetnik\w*|pravn\w* uslug\w*|pravno savjetovanje",
          "4007", "Pravne usluge", 1.0, "Porezno priznato u cijelosti."),
    _rule(r"knjigovodstv\w*",
          "4008", "Knjigovodstvene usluge", 1.0, "Porezno priznato u cijelosti."),
    _rule(r"cisceni\w*|cistac\w*",
          "4009", "Usluge čišćenja", 1.0, "Porezno priznato u cijelosti."),
    _rule(r"osiguranj\w*",
          "4010", "Osiguranje", 1.0, "Porezno priznato u cijelosti."),
    _rule(r"\bnajam\w*|\bzakup\w*",
          "4011", "Najam poslovnog prostora", 1.0, "Porezno priznato u cijelosti."),
    _rule(r"marketing\w*|\boglas\w*|reklam\w*|promocij\w*",
          "4012", "Marketing i oglašavanje", 1.0, "Porezno priznato u cijelosti."),
    _rule(r"edukacij\w*|\bseminar\w*|\btecaj\w*|radionic\w*|strucn\w* usavrsavanj\w*",
          "4013", "Edukacija i stručno usavršavanje", 1.0, "Porezno priznato u cijelosti."),
    _rule(r"racunal\w*|\blaptop\w*|softver\w*|licenc\w* za program|it oprema",
          "4014", "Računalna oprema i IT usluge", 1.0, "Porezno priznato u cijelosti."),
    _rule(r"dar\w* (poslovn\w*|klijent\w*|partner\w*)|poklon\w* (poslovn\w*|klijent\w*|partner\w*)",
          "4015", "Darovi poslovnim partnerima", 0.5, "Djelomično porezno priznato 50%, provjeri."),
]

_DEFAULT = {"konto": "6000", "naziv": "Ostali troškovi", "porezno_priznato": 1.0,
            "note": "Nisam siguran — provjeri ručno", "matched": False}


def categorize(description: str) -> dict:
    q = _norm(description)
    for rule in RULES:
        if rule["pattern"].search(q):
            return {"konto": rule["konto"], "naziv": rule["naziv"],
                    "porezno_priznato": rule["porezno_priznato"], "note": rule["note"],
                    "matched": True}
    return dict(_DEFAULT)
