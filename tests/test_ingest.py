import pytest
from atlas.docs import ingest as ing

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

def test_ingest_dedup_lost_race_returns_none(spine, monkeypatch):
    text = "Tekst za race-dedup test, ne smije puknuti."
    sha = ing._norm_sha(text)
    with spine.write() as c:
        c.execute(
            "INSERT INTO documents(title,path,doc_type,client_id,sha256,source_url) VALUES(?,?,?,?,?,?)",
            ("preexisting", "", "ostalo", None, sha, ""),
        )

    class _NoHit:
        def fetchone(self):
            return None

    class _NoCheck:
        def execute(self, *a, **k):
            return _NoHit()

    # simulate a concurrent writer winning the sha256 race: pre-check misses,
    # but the row is already there so the INSERT itself must hit the UNIQUE
    # constraint and ingest_text must still return None, not raise.
    monkeypatch.setattr(spine, "read", lambda: _NoCheck())
    assert ing.ingest_text(spine, text, "dup-race") is None

def test_ingest_hooks_only_swallow_importerror(spine, monkeypatch):
    import sys, types

    fake_embed = types.ModuleType("atlas.rag.embed")
    def _boom(spine, ids):
        raise RuntimeError("bad key")
    fake_embed.index_chunks = _boom
    fake_rag = types.ModuleType("atlas.rag")
    monkeypatch.setitem(sys.modules, "atlas.rag", fake_rag)
    monkeypatch.setitem(sys.modules, "atlas.rag.embed", fake_embed)

    # a real runtime error from a present embed module must propagate, not
    # be silently swallowed as if the module were merely absent.
    with pytest.raises(RuntimeError, match="bad key"):
        ing.ingest_text(spine, "Tekst gdje embed puca s pravom greškom.", "boom-doc")

def test_ingest_txt_file(spine, tmp_path):
    p = tmp_path / "a.txt"; p.write_text("Ugovor o radu sklopljen...", encoding="utf-8")
    assert ing.ingest_file(spine, str(p)) is not None

def test_bulk(spine, tmp_path):
    (tmp_path / "a.txt").write_text("dokument A" * 50, encoding="utf-8")
    (tmp_path / "b.md").write_text("dokument B" * 50, encoding="utf-8")
    r = ing.bulk_ingest(spine, str(tmp_path))
    assert r["ingested"] == 2
