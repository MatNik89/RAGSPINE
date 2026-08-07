import pytest

from atlas.core import optional
from atlas.docs import pdfforms

fitz = optional.need("fitz", "PDF forms")


def _make_form_pdf(path, field_name="naziv"):
    doc = fitz.open()
    page = doc.new_page()
    w = fitz.Widget()
    w.field_name = field_name
    w.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    w.rect = fitz.Rect(72, 72, 300, 100)
    w.field_value = ""
    page.add_widget(w)
    doc.save(path)
    doc.close()


@pytest.mark.skipif(fitz is None, reason="fitz not installed")
def test_fill_sets_widget_value(tmp_path):
    src = tmp_path / "form.pdf"
    out = tmp_path / "filled.pdf"
    _make_form_pdf(str(src))

    n = pdfforms.fill(str(src), {"naziv": "Firma X"}, str(out))

    assert n == 1
    doc = fitz.open(str(out))
    try:
        widgets = list(doc[0].widgets())
        assert widgets[0].field_value == "Firma X"
    finally:
        doc.close()


@pytest.mark.skipif(fitz is None, reason="fitz not installed")
def test_fill_ignores_unknown_fields(tmp_path):
    src = tmp_path / "form.pdf"
    out = tmp_path / "filled.pdf"
    _make_form_pdf(str(src))

    n = pdfforms.fill(str(src), {"nepostojece_polje": "x"}, str(out))

    assert n == 0


def test_fill_raises_without_fitz(monkeypatch, tmp_path):
    monkeypatch.setattr(pdfforms.optional, "need", lambda *a, **k: None)
    with pytest.raises(pdfforms.FormUnavailable):
        pdfforms.fill(str(tmp_path / "a.pdf"), {}, str(tmp_path / "b.pdf"))


def test_client_fields_maps_columns(spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name,oib,email,phone,owner) VALUES(?,?,?,?,?)",
                   ("Firma X", "12345678901", "x@firma.hr", "091", "Ana"))

    fields = pdfforms.client_fields(spine, 1)

    assert fields["naziv"] == "Firma X"
    assert fields["OIB"] == "12345678901"
    assert fields["email"] == "x@firma.hr"
    assert fields["telefon"] == "091"
    assert fields["vlasnik"] == "Ana"


def test_client_fields_missing_client_returns_empty(spine):
    assert pdfforms.client_fields(spine, 999) == {}
