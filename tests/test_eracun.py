import os
from pathlib import Path

import pytest

from ragspine.docs import eracun
from ragspine.rag import retrieval

FIXTURE = Path(__file__).parent / "fixtures" / "eracun_ubl.xml"


def test_parse_ubl():
    parsed = eracun.parse_ubl(FIXTURE.read_bytes())
    assert parsed["supplier_oib"] == "69435151530"
    assert parsed["customer_oib"] == "11111111119"
    assert parsed["total"] == 125.0
    assert parsed["vat"] == 25.0
    assert parsed["currency"] == "EUR"
    assert parsed["issued"] == "2026-07-15"
    assert len(parsed["lines"]) == 2
    assert parsed["lines"][0]["name"] == "Konzultantske usluge"
    assert parsed["lines"][0]["qty"] == 2.0
    assert parsed["lines"][0]["price"] == 30.0


def test_parse_ubl_malformed():
    with pytest.raises(ValueError):
        eracun.parse_ubl(b"<not><valid")


def test_store_inserts_and_is_searchable(spine):
    parsed = eracun.parse_ubl(FIXTURE.read_bytes())
    eid = eracun.store(spine, parsed, raw_path=str(FIXTURE))
    assert isinstance(eid, int)

    row = spine.read().execute("SELECT * FROM eracuni WHERE id=?", (eid,)).fetchone()
    assert row is not None
    assert row["supplier_oib"] == "69435151530"
    assert row["customer_oib"] == "11111111119"
    assert row["total"] == 125.0
    assert row["vat"] == 25.0
    assert row["currency"] == "EUR"
    assert row["raw_path"] == str(FIXTURE)

    hits = retrieval.search(spine, "69435151530")
    assert any("69435151530" in h.text for h in hits)


def _add_client(spine, oib, nas_folder):
    with spine.write() as c:
        cur = c.execute(
            "INSERT INTO clients(name, oib, nas_folder) VALUES (?, ?, ?)",
            ("Kupac", oib, nas_folder),
        )
    return cur.lastrowid


def test_autosort_moves_file(spine, cfg, tmp_path):
    cfg.nas_root = str(tmp_path / "nas")
    _add_client(spine, "11111111119", "klijenti/firma-a")
    xml_path = tmp_path / "src" / "e_racun.xml"
    xml_path.parent.mkdir(parents=True)
    xml_path.write_bytes(FIXTURE.read_bytes())

    dest = eracun.autosort(spine, cfg, str(xml_path))

    assert dest is not None
    dest_root = Path(os.path.realpath(cfg.nas_root))
    assert Path(os.path.realpath(dest)).is_relative_to(dest_root)
    assert Path(dest).exists()
    assert not xml_path.exists()


def test_autosort_blocks_path_traversal(spine, cfg, tmp_path):
    cfg.nas_root = str(tmp_path / "nas")
    _add_client(spine, "11111111119", "../../../etc")
    xml_path = tmp_path / "src" / "e_racun.xml"
    xml_path.parent.mkdir(parents=True)
    xml_path.write_bytes(FIXTURE.read_bytes())

    with pytest.raises(ValueError):
        eracun.autosort(spine, cfg, str(xml_path))

    assert xml_path.exists()


def test_autosort_unknown_client_notifies(spine, cfg, tmp_path):
    cfg.nas_root = str(tmp_path / "nas")
    xml_path = tmp_path / "src" / "e_racun.xml"
    xml_path.parent.mkdir(parents=True)
    xml_path.write_bytes(FIXTURE.read_bytes())

    dest = eracun.autosort(spine, cfg, str(xml_path))

    assert dest is None
    assert xml_path.exists()
    row = spine.read().execute(
        "SELECT * FROM notifications WHERE kind='eracun_unmatched'"
    ).fetchone()
    assert row is not None
