"""Generate quotes/letters/dunning notices from templates, LLM prose isolated
from computed numbers, and a post-render gate that catches numeric hallucination
(the LLM inventing/dropping money figures in a client-facing document)."""
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ragspine.core import optional


class DocUnavailable(Exception):
    pass


TEMPLATES = {
    "ponuda": {
        "naslov": "Ponuda",
        "prose_slots": {"uvod"},
        "template": (
            "PONUDA\n\n"
            "Klijent: {{klijent}}\nDatum: {{datum}}\n\n"
            "{{uvod}}\n\n"
            "Stavke:\n{{stavke}}\n\n"
            "Ukupno: {{ukupno}}\n"
        ),
    },
    "dopis": {
        "naslov": "Dopis",
        "prose_slots": {"tekst"},
        "template": (
            "DOPIS\n\n"
            "Klijent: {{klijent}}\nDatum: {{datum}}\nPredmet: {{predmet}}\n\n"
            "{{tekst}}\n"
        ),
    },
    "opomena": {
        "naslov": "Opomena",
        "prose_slots": {"tekst"},
        "template": (
            "OPOMENA PRED TUŽBU\n\n"
            "Klijent: {{klijent}}\nDatum: {{datum}}\n\n"
            "{{tekst}}\n\n"
            "Iznos duga: {{iznos_duga}}\nRok plaćanja: {{rok}}\n"
        ),
    },
}

# money slot names per doc type, used by generate() to collect expected numbers
_MONEY_SLOTS = {
    "ponuda": ["ukupno"],
    "dopis": [],
    "opomena": ["iznos_duga"],
}

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")
# HR thousands-dot/decimal-comma (1.234,56) OR plain (1234.56 / 1234,56)
_NUMBER_RE = re.compile(r"\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}|\d+\.\d{2}")


@dataclass
class GateReport:
    ok: bool
    missing: list = field(default_factory=list)
    found: list = field(default_factory=list)


def _fmt_money(value) -> str:
    d = Decimal(str(value)).quantize(Decimal("0.01"))
    s = f"{d:,.2f}"  # e.g. "1,234.56"
    s = s.replace(",", "_").replace(".", ",").replace("_", ".")  # -> "1.234,56"
    return f"{s} EUR"


def _parse_number(text: str) -> float:
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")  # HR: 1.234,56 -> 1234.56
    elif "," in text:
        text = text.replace(",", ".")  # 1234,56 -> 1234.56
    return float(text)


def _slot_number(value) -> float:
    """Numeric value of a (possibly already-formatted, e.g. '250,00 EUR') money slot."""
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    m = _NUMBER_RE.search(str(value))
    return _parse_number(m.group(0)) if m else _parse_number(str(value))


def _today() -> str:
    return date.today().strftime("%d.%m.%Y.")


def fill_template(doc_type: str, values: dict) -> str:
    if doc_type not in TEMPLATES:
        raise ValueError(f"nepoznat tip dokumenta: {doc_type}")
    template = TEMPLATES[doc_type]["template"]

    def _sub(m):
        key = m.group(1)
        return str(values[key]) if key in values else m.group(0)

    return _PLACEHOLDER_RE.sub(_sub, template)


def post_render_gate(rendered_text: str, expected_numbers: list, tol: float = 0.02) -> GateReport:
    found = [_parse_number(m.group(0)) for m in _NUMBER_RE.finditer(rendered_text)]
    missing = [
        float(exp) for exp in expected_numbers
        if not any(abs(float(exp) - f) <= tol for f in found)
    ]
    return GateReport(ok=not missing, missing=missing, found=found)


def generate(doc_type: str, values: dict, prose: dict | None = None) -> dict:
    if doc_type not in TEMPLATES:
        raise ValueError(f"nepoznat tip dokumenta: {doc_type}")
    prose_slots = TEMPLATES[doc_type]["prose_slots"]
    merged = dict(values)
    if prose:
        for key, text in prose.items():
            if key in prose_slots:  # LLM may ONLY fill declared prose slots
                merged[key] = text

    text = fill_template(doc_type, merged)

    expected = []
    for slot in _MONEY_SLOTS.get(doc_type, []):
        if slot in values:
            expected.append(_slot_number(values[slot]))
    if doc_type == "ponuda" and "_stavke_iznosi" in values:
        expected.extend(float(x) for x in values["_stavke_iznosi"])

    gate = post_render_gate(text, expected)
    return {"text": text, "gate": gate}


def to_docx(text: str, out_path: str) -> None:
    docx = optional.need("docx", "DOCX export")
    if docx is None:
        raise DocUnavailable("python-docx nije instaliran")
    doc = docx.Document()
    for line in text.splitlines():
        doc.add_paragraph(line)
    doc.save(out_path)


def generate_from_client(spine, doc_type: str, client_id: int,
                          extra: dict | None = None, llm=None) -> dict:
    if doc_type not in TEMPLATES:
        raise ValueError(f"nepoznat tip dokumenta: {doc_type}")
    row = spine.read().execute(
        "SELECT name, oib FROM clients WHERE id=?", (client_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"klijent {client_id} ne postoji")

    extra = extra or {}
    values = {"klijent": row["name"], "datum": _today(), "oib": row["oib"] or ""}
    prose_slots = TEMPLATES[doc_type]["prose_slots"]

    if doc_type == "ponuda":
        stavke = extra.get("stavke", [])
        iznosi = [float(s["iznos"]) for s in stavke]
        ukupno = sum(iznosi)
        values["stavke"] = "\n".join(f"- {s['naziv']}: {_fmt_money(s['iznos'])}" for s in stavke)
        values["ukupno"] = _fmt_money(ukupno)
        values["_stavke_iznosi"] = iznosi
        prompt = f"Napiši kratak profesionalan uvod za ponudu klijentu {row['name']}."
        default_prose = f"Poštovani {row['name']},\n\nu nastavku Vam dostavljamo ponudu."
    elif doc_type == "opomena":
        values["iznos_duga"] = _fmt_money(extra.get("iznos_duga", 0))
        values["rok"] = extra.get("rok", "")
        prompt = f"Napiši kratak profesionalan tekst opomene klijentu {row['name']}."
        default_prose = f"Poštovani {row['name']},\n\npodsjećamo na neplaćeni dug."
    else:  # dopis
        values["predmet"] = extra.get("predmet", "")
        prompt = f"Napiši kratak profesionalan dopis klijentu {row['name']}."
        default_prose = f"Poštovani {row['name']},"

    prose_key = next(iter(prose_slots))
    if llm is not None:
        prose_text = llm.complete([{"role": "user", "content": prompt}]).text
    else:
        prose_text = default_prose
    prose = {prose_key: prose_text}

    result = generate(doc_type, values, prose=prose)
    if not result["gate"].ok:
        result["warning"] = f"numeric gate FAILED: brojke nedostaju u dokumentu: {result['gate'].missing}"
    return result
