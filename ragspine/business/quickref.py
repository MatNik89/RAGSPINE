# Quickref — 24 brzo-pretraživih brojki (pragovi, stope, rokovi) s izvorom.
#
# ponytail: SEED vrijednosti su plauzibilni defaulti za HR 2026, ne
# garantirano ažurni. Operater ih drži svježima kroz
# spine.set_override("quickref", key, vrijednost); watchlist može predložiti
# promjenu kad izvor (NN/Porezna) objavi novu brojku.

_DIACRITICS = str.maketrans("čćžšđČĆŽŠĐ", "cczsdCCZSD")


def _norm(s: str) -> str:
    return (s or "").translate(_DIACRITICS).lower()


SEED: list[dict] = [
    {"key": "minimalac_bruto", "label": "Minimalna bruto plaća 2026", "value": "970.00",
     "unit": "EUR", "category": "place", "source": "Uredba o visini minimalne plaće, NN",
     "keywords": "minimalac minimalna placa bruto"},
    {"key": "pdv_prag", "label": "Prag ulaska u sustav PDV-a", "value": "60000",
     "unit": "EUR", "category": "pdv", "source": "Zakon o PDV-u",
     "keywords": "pdv prag registracija sustav"},
    {"key": "pdv_stopa_opca", "label": "PDV opća stopa", "value": "25",
     "unit": "%", "category": "pdv", "source": "Zakon o PDV-u",
     "keywords": "pdv stopa opca 25"},
    {"key": "pdv_stopa_snizena_13", "label": "PDV snižena stopa", "value": "13",
     "unit": "%", "category": "pdv", "source": "Zakon o PDV-u",
     "keywords": "pdv stopa snizena 13"},
    {"key": "pdv_stopa_snizena_5", "label": "PDV najniža stopa", "value": "5",
     "unit": "%", "category": "pdv", "source": "Zakon o PDV-u",
     "keywords": "pdv stopa snizena 5"},
    {"key": "neoporeziva_prigodna_nagrada", "label": "Neoporeziva prigodna nagrada (uskrsnica/božićnica)",
     "value": "700", "unit": "EUR", "category": "place",
     "source": "Pravilnik o porezu na dohodak",
     "keywords": "prigodna nagrada uskrsnica bozicnica neoporeziva"},
    {"key": "neoporezive_nagrade_radni_rezultati", "label": "Neoporezive nagrade za radne rezultate",
     "value": "1120", "unit": "EUR", "category": "place",
     "source": "Pravilnik o porezu na dohodak",
     "keywords": "nagrada radni rezultati neoporezivo"},
    {"key": "mio_1", "label": "Doprinos MIO I. stup", "value": "15",
     "unit": "%", "category": "doprinosi", "source": "Zakon o doprinosima",
     "keywords": "mio prvi stup doprinos mirovinsko"},
    {"key": "mio_2", "label": "Doprinos MIO II. stup", "value": "5",
     "unit": "%", "category": "doprinosi", "source": "Zakon o doprinosima",
     "keywords": "mio drugi stup doprinos mirovinsko"},
    {"key": "zdravstveno", "label": "Doprinos za zdravstveno osiguranje", "value": "16.5",
     "unit": "%", "category": "doprinosi", "source": "Zakon o doprinosima",
     "keywords": "zdravstveno osiguranje doprinos hzzo"},
    {"key": "studentska_satnica", "label": "Minimalna satnica studentskog posla", "value": "5.83",
     "unit": "EUR/h", "category": "place", "source": "Odluka o satnici studentskog rada",
     "keywords": "studentski posao satnica ugovor"},
    {"key": "djecji_doplatak_cenzus", "label": "Cenzus za dječji doplatak", "value": "621.29",
     "unit": "EUR/mj po članu", "category": "socijalno", "source": "Zakon o doplatku za djecu",
     "keywords": "djecji doplatak cenzus dijete"},
    {"key": "neoporeziva_otpremnina", "label": "Neoporeziva otpremnina po godini staža", "value": "897.16",
     "unit": "EUR/godina staža", "category": "place", "source": "Pravilnik o porezu na dohodak",
     "keywords": "otpremnina neoporeziva raskid ugovora"},
    {"key": "bozicnica_regres", "label": "Neoporezivi regres/božićnica", "value": "700",
     "unit": "EUR", "category": "place", "source": "Pravilnik o porezu na dohodak",
     "keywords": "bozicnica regres godisnji odmor neoporezivo"},
    {"key": "dar_djetetu", "label": "Neoporezivi dar djetetu do 15 godina", "value": "140",
     "unit": "EUR", "category": "place", "source": "Pravilnik o porezu na dohodak",
     "keywords": "dar dijete 15 godina neoporezivo bozic"},
    {"key": "terenski_dodatak", "label": "Neoporezivi terenski dodatak", "value": "20",
     "unit": "EUR/dan", "category": "place", "source": "Pravilnik o porezu na dohodak",
     "keywords": "terenski dodatak rad na terenu"},
    {"key": "kilometraza", "label": "Naknada za korištenje privatnog automobila", "value": "0.50",
     "unit": "EUR/km", "category": "place", "source": "Pravilnik o porezu na dohodak",
     "keywords": "kilometraza km naknada privatni automobil"},
    {"key": "zatezna_kamata", "label": "Zakonska stopa zateznih kamata", "value": "7.5",
     "unit": "%", "category": "financije", "source": "Zakon o financijskom poslovanju i predstečajnoj nagodbi",
     "keywords": "zatezna kamata stopa zakonska"},
    {"key": "pausal_razred_1", "label": "Paušalni obrt — 1. razred (do)", "value": "40000",
     "unit": "EUR/god", "category": "obrt", "source": "Pravilnik o paušalnom oporezivanju obrta",
     "keywords": "pausal obrt razred prihod prag"},
    {"key": "pausal_razred_2", "label": "Paušalni obrt — 2. razred (do)", "value": "60000",
     "unit": "EUR/god", "category": "obrt", "source": "Pravilnik o paušalnom oporezivanju obrta",
     "keywords": "pausal obrt razred prihod prag"},
    {"key": "pausal_razred_3", "label": "Paušalni obrt — 3. razred (do)", "value": "80000",
     "unit": "EUR/god", "category": "obrt", "source": "Pravilnik o paušalnom oporezivanju obrta",
     "keywords": "pausal obrt razred prihod prag"},
    {"key": "joppd_rok", "label": "Rok predaje JOPPD obrasca", "value": "isplata + 1 radni dan",
     "unit": "rok", "category": "rokovi", "source": "Pravilnik o poreznom postupku",
     "keywords": "joppd rok predaje obrazac"},
    {"key": "pdv_rok", "label": "Rok predaje PDV obrasca", "value": "20. u mjesecu",
     "unit": "rok", "category": "rokovi", "source": "Zakon o PDV-u",
     "keywords": "pdv rok predaje obrazac"},
    {"key": "doh_rok", "label": "Rok predaje godišnje prijave poreza na dohodak", "value": "28.2.",
     "unit": "rok", "category": "rokovi", "source": "Zakon o porezu na dohodak",
     "keywords": "doh rok prijava godisnja porez dohodak"},
]


def seed(spine) -> int:
    n = 0
    with spine.write() as c:
        for item in SEED:
            cur = c.execute(
                """INSERT OR IGNORE INTO quickref(key,label,value,unit,category,source,keywords)
                   VALUES(?,?,?,?,?,?,?)""",
                (item["key"], item["label"], item["value"], item["unit"],
                 item["category"], item["source"], item["keywords"]),
            )
            n += cur.rowcount
    return n


def search(spine, term: str) -> list[dict]:
    t = f"%{_norm(term)}%"
    rows = spine.read().execute(
        """SELECT key,label,value,unit,category,source,keywords FROM quickref
           WHERE replace(replace(replace(replace(replace(lower(key),'č','c'),'ć','c'),'ž','z'),'š','s'),'đ','d') LIKE ?
              OR replace(replace(replace(replace(replace(lower(label),'č','c'),'ć','c'),'ž','z'),'š','s'),'đ','d') LIKE ?
              OR replace(replace(replace(replace(replace(lower(keywords),'č','c'),'ć','c'),'ž','z'),'š','s'),'đ','d') LIKE ?""",
        (t, t, t),
    ).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        override = spine.get_override("quickref", d["key"])
        if override is not None:
            d["value"] = override
        out.append(d)
    return out
