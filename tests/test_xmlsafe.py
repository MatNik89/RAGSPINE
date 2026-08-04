import pytest
import xml.etree.ElementTree as ET

from ragspine.core import xmlsafe


def test_plain_xml_parses():
    el = xmlsafe.fromstring(b"<a><b>x</b></a>")
    assert el.findtext("b") == "x"


def test_doctype_blocked():
    with pytest.raises(xmlsafe.XmlBlocked):
        xmlsafe.fromstring(b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x "y">]><r>&x;</r>')


def test_entity_blocked_even_with_whitespace():
    with pytest.raises(xmlsafe.XmlBlocked):
        xmlsafe.fromstring(b"<!  ENTITY evil>")


def test_xmlblocked_is_valueerror():
    assert issubclass(xmlsafe.XmlBlocked, ValueError)


def test_malformed_still_parseerror():
    with pytest.raises(ET.ParseError):
        xmlsafe.fromstring(b"<not><closed")


def test_utf16_doctype_blocked():
    doc = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE r [<!ENTITY x "y">]><r>&x;</r>'
    for enc in ("utf-16", "utf-16-le", "utf-16-be"):
        import pytest as _pt
        with _pt.raises(xmlsafe.XmlBlocked):
            xmlsafe.fromstring(doc.encode(enc))
