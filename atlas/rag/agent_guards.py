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
    završava usred tokena (inače model izmišlja 'popravak'). Fidelity-nota jasno
    kaže da je nepotpuno (model NE parsira kao valjan JSON). Ukupno <= limit
    (nota rezervirana; Codex). Rez može biti unutar stringa — nota to signalizira."""
    if text is None or len(text) <= limit:
        return text or ""
    budget = max(1, limit - len(_FIDELITY))
    cut = text[:budget]
    for sep in _STRUCT_SEPS:
        i = cut.rfind(sep)
        if i > budget * 0.5:
            return cut[:i + len(sep)] + _FIDELITY
    return cut + _FIDELITY


def observed_oibs(text: str) -> set:
    """OIB-ovi viđeni u sadržaju (za evidence-akumulaciju; jeftino, set ne string)."""
    return set(_OIB_RE.findall(text or ""))


def unverified_oibs(answer: str, observed) -> list[str]:
    """Vrati VALJANE OIB-ove koje odgovor navodi a NISU u skupu viđenih (`observed`
    = set OIB-stringova iz rezultata alata + upita). Hvata ČISTU izmišljotinu (OIB
    koji se ne pojavljuje nigdje); NE hvata krivo-pripisivanje (točan OIB, kriv
    klijent) — to je entity-binding, širi zahvat. OIB=11 znam.+checksum = visoka
    preciznost."""
    ans = {o for o in _OIB_RE.findall(answer or "") if security.oib_valid(o)}
    if not ans:
        return []
    obs = observed if isinstance(observed, (set, frozenset)) else set(_OIB_RE.findall(observed or ""))
    return sorted(ans - obs)


def loop_key(name, args: dict) -> str:
    """Potpis poziva (alat+argumenti) — ponovljeni isti = bez napretka. Podnosi
    name=None (malformiran poziv; Codex)."""
    n = str(name or "")
    try:
        return n + "|" + json.dumps(args or {}, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return n + "|" + str(args)


def append_evidence_caution(text: str, unverified: list[str]) -> str:
    if not unverified:
        return text
    lista = ", ".join(unverified)
    return (text or "") + ("\n\n⚠ Nisam iz podataka potvrdio OIB: " + lista +
                           " — provjerite prije korištenja.")
