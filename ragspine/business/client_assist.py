# AI sidebar za DODAJ NOVOG KLIJENTA wizard (piece F): gleda draft dok se
# tipka i vraća — deterministička pravila (brojke iz quickref registra, NE
# hardkod), popis obrazaca institucija za situaciju, RAG citate iz propisa i
# (opcionalno) kratki LLM sažetak grundan ISKLJUČIVO na tim izvorima.
# Bez LLM-a radi jednako — samo bez sažetka (isti hibridni duh kao C3).

import logging

from ragspine.core import security

_log = logging.getLogger(__name__)

LEGAL_FORMS = ("", "obrt", "poduzece")

# Standardni koraci/obrasci pri otvaranju — kratko i općenito; detalji i
# aktualne brojke dolaze iz RAG citata i quickref registra, ne odavde.
_FORMS = {
    "obrt": ["Upis u Obrtni registar (obrtnica)",
             "Prijava u registar poreznih obveznika (RPO / Porezna uprava)",
             "Prijava vlasnika u HZMO i HZZO"],
    "poduzece": ["Rješenje trgovačkog suda (sudski registar)",
                 "Prijava u registar poreznih obveznika (Porezna uprava)",
                 "Prijava direktora u HZMO (ako je zaposlen u društvu)"],
    "employees": ["Prijava radnika u HZMO (e-prijava)",
                  "JOPPD obrazac (mjesečno, uz isplatu plaće)"],
    "pdv": ["Zahtjev za registriranje za potrebe PDV-a (P-PDV)"],
}


def _qref(spine, key: str) -> dict | None:
    r = spine.read().execute(
        "SELECT key, label, value, unit, source FROM quickref WHERE key=?", (key,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    override = spine.get_override("quickref", key)
    if override is not None:
        d["value"] = override
    return d


def _rules(spine, draft: dict) -> tuple[list[str], list[str], list[str], list[str]]:
    """(warnings, suggestions, forms, rag_queries) iz drafta."""
    warnings, suggestions, forms, queries = [], [], [], []
    legal = (draft.get("legal_form") or "").strip()
    regime = (draft.get("regime") or "").strip()
    oib = (draft.get("oib") or "").strip()

    if legal and legal not in LEGAL_FORMS:
        warnings.append(f"Nepoznat pravni oblik: {legal!r} (obrt ili poduzece).")
    if not oib:
        suggestions.append("Unesi OIB — ključan za e-račune, automatsko sortiranje "
                           "dokumenata i buduće povezivanje sa satnicom/plaćama.")
    elif not security.oib_valid(oib):
        warnings.append("OIB ne prolazi kontrolnu znamenku — provjeri broj.")

    if legal == "poduzece" and regime in ("dohodak", "pausal"):
        warnings.append("Poduzeće (d.o.o./j.d.o.o.) ne može biti na dohotku/paušalu — "
                        "obračun ide na porez na dobit.")
    if legal == "poduzece":
        forms += _FORMS["poduzece"]
    elif legal == "obrt":
        forms += _FORMS["obrt"]

    if regime == "pausal":
        razredi = [q for k in ("pausal_razred_1", "pausal_razred_2", "pausal_razred_3")
                   if (q := _qref(spine, k))]
        if razredi:
            pragovi = ", ".join(f"{q['value']} {q['unit']}" for q in razredi)
            suggestions.append(f"Paušalni razredi (pragovi prihoda): {pragovi} — "
                               f"izvor: {razredi[0]['source']}.")
        queries.append("paušalno oporezivanje obrta uvjeti i razredi")

    if not (draft.get("pdv_status") or "").strip():
        q = _qref(spine, "pdv_prag")
        if q:
            suggestions.append(f"Odredi PDV status — prag ulaska u sustav: "
                               f"{q['value']} {q['unit']} ({q['source']}).")
        forms += _FORMS["pdv"] if legal else []
        queries.append("prag ulaska u sustav PDV-a registracija")

    if draft.get("has_employees"):
        forms += _FORMS["employees"]
        queries.append("obveze poslodavca prijava radnika")

    return warnings, suggestions, forms, queries


def assist(spine, cfg, draft: dict, llm=None) -> dict:
    warnings, suggestions, forms, queries = _rules(spine, draft)

    sources = []
    try:
        from ragspine.rag import retrieval
        seen = set()
        for q in queries[:3]:
            for hit in retrieval.search(spine, q, k=2):
                key = (hit.title, hit.text[:80])
                if key in seen:
                    continue
                seen.add(key)
                sources.append({"title": hit.title, "snippet": hit.text[:240]})
        sources = sources[:4]
    except Exception:  # RAG je best-effort — sidebar nikad ne ruši tipkanje
        sources = []

    llm_note = None
    if llm is not None and (warnings or suggestions or sources):
        ctx = "\n".join(f"- {s['title']}: {s['snippet']}" for s in sources)
        system = ("Ti si asistent knjigovodstvenog ureda. Na temelju ISKLJUČIVO danih "
                  "izvora i navedenih točaka, napiši 2-3 kratke rečenice savjeta na "
                  "hrvatskom za otvaranje ovog klijenta. Ne izmišljaj brojke ni propise.")
        prompt = (f"Klijent: {draft}\nTočke: {warnings + suggestions}\nIzvori:\n{ctx or '(nema)'}")
        try:
            llm_note = llm.complete([{"role": "user", "content": prompt}],
                                    system=system).text.strip()[:600]
        except Exception as e:
            _log.warning("assist LLM preskočen: %s", e)

    # dedup uz očuvan redoslijed
    forms = list(dict.fromkeys(forms))
    return {"warnings": warnings, "suggestions": suggestions, "forms": forms,
            "sources": sources, "llm_note": llm_note}
