"""Stdlib XML parse s odbijanjem DTD/entiteta — ubija billion-laughs
(entity-expansion) i external-entity XXE, koje bi expat inače proširio.

ponytail: byte-razina DOCTYPE/ENTITY reject umjesto defusedxml ovisnosti
(nema nove ovisnosti, čisti stdlib). Upgrade path = defusedxml ako ikad
treba prihvatiti legitiman DTD. Teoretski false-positive: CDATA koji doslovno
sadrži bajtove `<!ENTITY` — nemoguće u UBL/RSS-u, a fail-closed je siguran smjer."""
import re
import xml.etree.ElementTree as ET

_DTD_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


class XmlBlocked(ValueError):
    """XML sadrži DTD/entity definiciju — odbijeno prije parsiranja."""


def fromstring(xml_bytes: bytes) -> ET.Element:
    # Null-strip prije pretrage: UTF-16 kodiran `<!DOCTYPE` ima interleaved \x00
    # (`<\x00!\x00D...`) koji bi ASCII regex promašio, a expat bi ga svejedno
    # proširio. XML 1.0 sadržaj ne smije imati sirovi \x00, pa je strip siguran.
    if _DTD_RE.search(xml_bytes.replace(b"\x00", b"")):
        raise XmlBlocked("XML DTD/entity blokiran (moguć XXE / entity-expansion)")
    return ET.fromstring(xml_bytes)
