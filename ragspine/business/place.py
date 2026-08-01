# Bruto -> neto kalkulator plaće (HR 2026).
#
# ponytail: nema umanjenja MIO doprinosa za niske plaće (mjesečni prag) i nema
# neoporezivih primitaka (bonusi, prehrana, prijevoz...). Upgrade path: dodati
# nove override ključeve (npr. "mio_reduction", "neoporezivo.{vrsta}") kad
# zatreba puna preciznost.

OSNOVNI_ODBITAK = 600.0
DIJETE_FAKTORI = [0.5, 0.7, 1.0]  # 1., 2., 3. dijete; 4.+ nije modeliran
INVALIDNOST_FAKTOR = 0.3
PRAG_OSNOVICE = 5000.0
STOPA_NIZA_DEFAULT = 20.0
STOPA_VISA_DEFAULT = 30.0


def bruto_to_neto(bruto: float, city: str = "", children: int = 0,
                   invalidnost: bool = False, spine=None) -> dict:
    detalji = []

    doprinosi = round(bruto * 0.20, 2)
    detalji.append(f"doprinosi 20% (MIO I 15 + MIO II 5) = {doprinosi}")

    dohodak = bruto - doprinosi

    odbitak = OSNOVNI_ODBITAK
    for i in range(min(children, len(DIJETE_FAKTORI))):
        odbitak += DIJETE_FAKTORI[i] * OSNOVNI_ODBITAK
    if invalidnost:
        odbitak += INVALIDNOST_FAKTOR * OSNOVNI_ODBITAK
    detalji.append(f"osobni odbitak = {odbitak}")

    osnovica = max(0.0, dohodak - odbitak)

    stopa_niza = STOPA_NIZA_DEFAULT
    stopa_visa = STOPA_VISA_DEFAULT
    if spine is not None:
        stopa_niza = float(spine.get_override("kalkulator", f"porez_niza.{city}", STOPA_NIZA_DEFAULT))
        stopa_visa = float(spine.get_override("kalkulator", f"porez_visa.{city}", STOPA_VISA_DEFAULT))

    niza_osnovica = min(osnovica, PRAG_OSNOVICE)
    visa_osnovica = max(0.0, osnovica - PRAG_OSNOVICE)
    porez = niza_osnovica * stopa_niza / 100 + visa_osnovica * stopa_visa / 100
    detalji.append(f"porez: {niza_osnovica}@{stopa_niza}% + {visa_osnovica}@{stopa_visa}%")

    # legacy prirez (ukinut u HR 2024., ali override se poštuje radi kompatibilnosti)
    if spine is not None:
        prirez = spine.get_override("kalkulator", f"prirez.{city}", None)
        if prirez is not None:
            porez *= 1 + float(prirez) / 100
            detalji.append(f"legacy prirez {prirez}% primijenjen")

    porez = round(porez, 2)
    neto = round(dohodak - porez, 2)

    return {
        "bruto": bruto,
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
