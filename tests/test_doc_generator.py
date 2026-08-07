import pytest

from atlas.docs import doc_generator as dg


def test_fill_template_replaces_placeholders():
    text = dg.fill_template("dopis", {"klijent": "Firma X", "datum": "1.8.2026.",
                                        "predmet": "Podsjetnik", "tekst": "Poštovani,"})
    assert "Firma X" in text
    assert "Podsjetnik" in text


def test_fill_template_unknown_type_raises():
    with pytest.raises(ValueError):
        dg.fill_template("nepostojeci", {})


def test_fill_template_leaves_unknown_placeholder():
    # "opomena" template references {{rok}}; omit it -> stays literal
    text = dg.fill_template("opomena", {"klijent": "X", "datum": "1.8.2026.",
                                          "iznos_duga": "100,00 EUR"})
    assert "{{rok}}" in text


def test_prose_cannot_fill_computed_slot():
    values = {"klijent": "Firma X", "datum": "1.8.2026.",
              "stavke": "Usluga A - 100,00 EUR", "ukupno": dg._fmt_money(100)}
    result = dg.generate("ponuda", values, prose={"ukupno": "999999", "uvod": "Poštovani, ..."})
    assert "999999" not in result["text"]
    assert dg._fmt_money(100) in result["text"]
    assert "Poštovani" in result["text"]


def test_generate_gate_pass_when_total_present():
    values = {"klijent": "Firma X", "datum": "1.8.2026.",
              "stavke": "Usluga A - 100,00 EUR", "ukupno": dg._fmt_money(100)}
    result = dg.generate("ponuda", values, prose={"uvod": "Kratki uvod."})
    assert result["gate"].ok is True


def test_post_render_gate_missing_number():
    report = dg.post_render_gate("Tekst bez brojke", [1234.56])
    assert report.ok is False
    assert 1234.56 in report.missing


def test_post_render_gate_parses_hr_format():
    report = dg.post_render_gate("Ukupno: 1.234,56 EUR", [1234.56])
    assert report.ok is True


def test_post_render_gate_hr_format_mismatch():
    report = dg.post_render_gate("Ukupno: 1.234,56 EUR", [9999.0])
    assert report.ok is False


def test_post_render_gate_plain_format():
    report = dg.post_render_gate("Total 1234.56 EUR", [1234.56])
    assert report.ok is True


def test_post_render_gate_does_not_tear_number_from_date():
    # "01.08.2026." must NOT be misread as the money figure 1.08
    report = dg.post_render_gate("Datum 01.08.2026. Ukupno 250,00 EUR", [1.08])
    assert report.ok is False
    assert 1.08 in report.missing

    report2 = dg.post_render_gate("Datum 01.08.2026. Ukupno 250,00 EUR", [250.00])
    assert report2.ok is True


def test_generate_from_client_computes_ukupno(spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name,oib,email,phone,owner) VALUES(?,?,?,?,?)",
                   ("Firma X", "12345678901", "x@firma.hr", "091", "Ana"))

    result = dg.generate_from_client(spine, "ponuda", 1, extra={
        "stavke": [{"naziv": "Usluga A", "iznos": 100}, {"naziv": "Usluga B", "iznos": 50.5}]
    })

    assert result["gate"].ok is True
    assert "Firma X" in result["text"]
    assert dg._fmt_money(150.5) in result["text"]
    assert "warning" not in result


def test_generate_from_client_opomena(spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name,oib,email,phone,owner) VALUES(?,?,?,?,?)",
                   ("Firma Y", "22345678901", "y@firma.hr", "092", "Ivo"))

    result = dg.generate_from_client(spine, "opomena", 1, extra={"iznos_duga": 250, "rok": "8 dana"})

    assert result["gate"].ok is True
    assert "Firma Y" in result["text"]
    assert dg._fmt_money(250) in result["text"]


def test_generate_from_client_missing_client_raises(spine):
    with pytest.raises(ValueError):
        dg.generate_from_client(spine, "ponuda", 999, extra={"stavke": []})


def test_generate_from_client_malformed_stavka_raises(spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name,oib,email,phone,owner) VALUES(?,?,?,?,?)",
                   ("Firma X", "12345678901", "x@firma.hr", "091", "Ana"))

    with pytest.raises(ValueError):
        dg.generate_from_client(spine, "ponuda", 1, extra={"stavke": [{"naziv": "X"}]})


def test_generate_warns_on_gate_failure(monkeypatch, spine):
    with spine.write() as c:
        c.execute("INSERT INTO clients(name,oib,email,phone,owner) VALUES(?,?,?,?,?)",
                   ("Firma Z", "32345678901", "z@firma.hr", "093", "Iva"))
    # force a broken template that drops the money slot, to simulate gate failure
    monkeypatch.setitem(dg.TEMPLATES, "opomena",
                          {**dg.TEMPLATES["opomena"], "template": "Poštovani {{klijent}}, {{tekst}}"})

    result = dg.generate_from_client(spine, "opomena", 1, extra={"iznos_duga": 250, "rok": "8 dana"})

    assert result["gate"].ok is False
    assert "warning" in result
    assert "brojke nedostaju" in result["warning"]


def test_to_docx_without_docx_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(dg.optional, "need", lambda *a, **k: None)
    with pytest.raises(dg.DocUnavailable):
        dg.to_docx("neki tekst", str(tmp_path / "out.docx"))


def test_to_docx_writes_file(tmp_path):
    docx = dg.optional.need("docx", "DOCX export")
    if docx is None:
        pytest.skip("python-docx not installed")
    out = tmp_path / "out.docx"
    dg.to_docx("Prvi red\nDrugi red", str(out))
    assert out.exists()
