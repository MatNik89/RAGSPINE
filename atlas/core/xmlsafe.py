"""Stdlib XML parse that rejects DTD/entities — kills billion-laughs
(entity-expansion) and external-entity XXE, which expat would otherwise expand.

ponytail: byte-level DOCTYPE/ENTITY reject instead of a defusedxml dependency
(no new dependency, pure stdlib). Upgrade path = defusedxml if a legitimate DTD
ever needs to be accepted. Theoretical false-positive: CDATA that literally
contains the bytes `<!ENTITY` — impossible in UBL/RSS, and fail-closed is the safe direction."""
import re
import xml.etree.ElementTree as ET

_DTD_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


class XmlBlocked(ValueError):
    """XML contains a DTD/entity definition — rejected before parsing."""


def fromstring(xml_bytes: bytes) -> ET.Element:
    # Null-strip before searching: UTF-16 encoded `<!DOCTYPE` has interleaved \x00
    # (`<\x00!\x00D...`) that an ASCII regex would miss, but expat would still
    # expand. XML 1.0 content must not contain a raw \x00, so the strip is safe.
    if _DTD_RE.search(xml_bytes.replace(b"\x00", b"")):
        raise XmlBlocked("XML DTD/entity blokiran (moguć XXE / entity-expansion)")
    return ET.fromstring(xml_bytes)
