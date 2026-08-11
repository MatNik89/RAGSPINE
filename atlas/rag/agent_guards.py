"""Zaštite agentske petlje (MateClaw obrasci, prilagođeni ATLAS-ovom jednostavnom
loopu): (1) StructuredTruncator — rez tool-rezultata na JSON-granici + fidelity-nota
(da model ne "popravi"/izmisli odsječeno); (2) evidence-guard — OIB u odgovoru koji
NIJEDAN alat nije stvarno vidio = neprovjeren (anti-halucinacija za računovodstvo);
(3) loop-guard ključ (isti alat+argumenti ponovljen = bez napretka)."""
import json
import re

from atlas.core import security

_FIDELITY = "\n…[skraćeno — izostavljeni dio NIJE poznat; ne izmišljaj nastavak]"
_OIB_RE = re.compile(r"\b\d{11}\b")
_STRUCT_SEPS = ("},", "],", "}", "]", ",")


def truncate_structured(text: str, limit: int = 6000) -> str:
    """Rez na zadnjoj strukturnoj granici (,/}/]) unutar limita — retained dio ne
    završava usred tokena (inače model izmišlja 'popravak'). Uz fidelity-notu."""
    if text is None or len(text) <= limit:
        return text or ""
    cut = text[:limit]
    for sep in _STRUCT_SEPS:
        i = cut.rfind(sep)
        if i > limit * 0.5:
            return cut[:i + len(sep)] + _FIDELITY
    return cut + _FIDELITY


def unverified_oibs(answer: str, observed: str) -> list[str]:
    """Vrati VALJANE OIB-ove koje odgovor navodi a nisu se pojavili ni u jednom
    rezultatu alata (observed) — kandidati za halucinaciju. Visoka preciznost:
    OIB je 11 znamenki + checksum, rijetko slučajno."""
    ans = {o for o in _OIB_RE.findall(answer or "") if security.oib_valid(o)}
    if not ans:
        return []
    obs = set(_OIB_RE.findall(observed or ""))
    return sorted(ans - obs)


def loop_key(name: str, args: dict) -> str:
    """Potpis poziva (alat+argumenti) — ponovljeni isti = bez napretka."""
    try:
        return name + "|" + json.dumps(args or {}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return name + "|" + str(args)


def append_evidence_caution(text: str, unverified: list[str]) -> str:
    if not unverified:
        return text
    lista = ", ".join(unverified)
    return (text or "") + ("\n\n⚠ Nisam iz podataka potvrdio OIB: " + lista +
                           " — provjerite prije korištenja.")
