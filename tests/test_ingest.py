from ragspine.docs import ingest as ing

def test_chunker_no_word_split():
    text = " ".join(["riječ%d" % i for i in range(2000)])
    chunks = ing.chunk_text(text, size=500, overlap=50)
    assert all(len(c) <= 600 for c in chunks)
    assert all(not c.startswith(" ") for c in chunks)
    joined = " ".join(chunks)
    assert "riječ1999" in joined

def test_detect_type():
    assert ing.detect_doc_type("Račun br. 55/2026, PDV 25%") == "racun"
    assert ing.detect_doc_type("Članak 1. Ovim se Zakonom uređuje...") == "zakon"
    assert ing.detect_doc_type("bla") == "ostalo"

def test_ingest_and_dedup(spine):
    d1 = ing.ingest_text(spine, "Zakon o PDV-u, stopa 25%.", "zakon-pdv")
    assert d1 is not None
    assert ing.ingest_text(spine, "Zakon o PDV-u, stopa 25%.", "zakon-pdv") is None
    n = spine.read().execute("SELECT COUNT(*) FROM chunks WHERE doc_id=?", (d1,)).fetchone()[0]
    assert n >= 1

def test_ingest_txt_file(spine, tmp_path):
    p = tmp_path / "a.txt"; p.write_text("Ugovor o radu sklopljen...")
    assert ing.ingest_file(spine, str(p)) is not None

def test_bulk(spine, tmp_path):
    (tmp_path / "a.txt").write_text("dokument A" * 50)
    (tmp_path / "b.md").write_text("dokument B" * 50)
    r = ing.bulk_ingest(spine, str(tmp_path))
    assert r["ingested"] == 2
