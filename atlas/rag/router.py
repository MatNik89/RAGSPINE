"""Query router: classifies a user query into one of 9 retrieval lanes.

Rules are checked in order; the first match wins. Unmatched queries
default to "chat" (plain LLM answer, no retrieval).

Croatian text is normalized (diacritics stripped, lowercased) before
matching, so a single ASCII rule covers both spellings, e.g. "knjiz"
matches "proknjiži" and "proknjizi" alike.
"""
import re

LANES = (
    "sql",
    "web",
    "graph",
    "ocr",
    "learn",
    "knjizenje",
    "arhitektura",
    "flota",
    "no_retrieval",
    "reject",
    "chat",
)

_DIACRITICS = str.maketrans("čćžšđČĆŽŠĐ", "cczsdCCZSD")


def _normalize(query: str) -> str:
    return query.translate(_DIACRITICS).lower()


def _rule(pattern: str, lane: str) -> tuple[re.Pattern, str]:
    return re.compile(pattern, re.IGNORECASE), lane


RULES: list[tuple[re.Pattern, str]] = [
    # --- reject: destructive commands / prompt injection — checked first ---
    _rule(r"izbrisi bazu", "reject"),
    _rule(r"obrisi sve", "reject"),
    _rule(r"drop table", "reject"),
    _rule(r"ignore previous", "reject"),
    _rule(r"zanemari (prethodne|sve) upute", "reject"),
    _rule(r"delete from", "reject"),
    _rule(r"rm -rf", "reject"),

    # --- sql: counts / sums / aggregates over structured data ---
    _rule(r"koliko (racuna|dokumenata|klijenata|stavki|artikala|faktura)", "sql"),
    _rule(r"zbroj", "sql"),
    _rule(r"ukupno.*(pdv|iznos|trosak|prihod)", "sql"),
    _rule(r"top \d* ?klijen", "sql"),
    _rule(r"broj (racuna|klijenata|dokumenata)", "sql"),
    _rule(r"prosje(k|cna) (vrijednost|iznos)", "sql"),
    _rule(r"lista (svih )?(klijenata|racuna|dokumenata)", "sql"),
    _rule(r"najveci klijent", "sql"),
    _rule(r"grupiraj po", "sql"),

    # --- learn: ingest a new source into the corpus ---
    _rule(r"nauci\s*(s|sa|iz)?\s*https?", "learn"),
    _rule(r"zapamti (stranicu|url)", "learn"),
    _rule(r"nauci (novi )?dokument", "learn"),
    _rule(r"dodaj (novi )?izvor", "learn"),
    _rule(r"ucitaj i zapamti", "learn"),

    # --- web: live internet search ---
    _rule(r"(pretrazi|potrazi).*(web|internet)", "web"),
    _rule(r"sto je novo na webu", "web"),
    _rule(r"provjeri na internetu", "web"),
    _rule(r"googlea?j", "web"),
    _rule(r"pretrazi (mrezu|net)", "web"),

    # --- ocr: scan / read text from images or scanned documents ---
    _rule(r"\bocr\b", "ocr"),
    _rule(r"skenir", "ocr"),
    _rule(r"prepoznaj tekst", "ocr"),
    _rule(r"digitaliziraj (racun|dokument)", "ocr"),
    _rule(r"citaj (skeniran|sliku)", "ocr"),

    # --- knjizenje: bookkeeping / accounting-entry guidance ---
    _rule(r"(kako|gdje|na koj\w*).*(proknjiz|knjiz)", "knjizenje"),
    _rule(r"\bkonto\b", "knjizenje"),
    _rule(r"kontiranje", "knjizenje"),
    _rule(r"temeljnic", "knjizenje"),

    # --- arhitektura: agreement on folder structure — ONLY explicit intents;
    # broad substrings ("struktura mape kontnog plana", "dogovor uredskog
    # najma", "koje su mape po klijentu oporezive") must not hijack chat ---
    _rule(r"dogovor\s+map[ae]\s+po\s+klijentu", "arhitektura"),
    _rule(r"dogovor\s+uredsk\w*\s+map", "arhitektura"),
    _rule(r"arhitektur\w*\s+map", "arhitektura"),

    # --- graph: relationships between entities ---
    _rule(r"povezan", "graph"),
    _rule(r"veza izmedu", "graph"),
    _rule(r"\bgraf\b", "graph"),
    _rule(r"odnos (klijenta|dobavljaca)", "graph"),
    _rule(r"mreza (klijenata|veza)", "graph"),

    # --- flota: launch a program on the worker's station ---
    _rule(r"kod\s+\S+\s+otvori\s+\S", "flota"),

    # --- no_retrieval: small talk, no lookup needed ---
    _rule(r"^(pozdrav|bok|hvala|dobro jutro|dobar dan)!?\??$", "no_retrieval"),
    _rule(r"^(cao|hej|hi)!?$", "no_retrieval"),
    _rule(r"kako si", "no_retrieval"),
]


def route(query: str) -> str:
    q = _normalize(query)
    for pattern, lane in RULES:
        if pattern.search(q):
            return lane
    return "chat"
