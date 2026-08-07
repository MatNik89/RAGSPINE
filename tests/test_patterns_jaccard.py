from atlas.knowledge import patterns


# --- _keywords ---

def test_keywords_drops_stopwords_and_diacritics():
    kw = patterns._keywords("Koliki je prirez za Split?")
    assert kw == {"prirez", "split"}


def test_keywords_strip_diacritics():
    kw = patterns._keywords("Koja je važeća kamatna stopa?")
    assert "vazeca" in kw
    assert "stopa" in kw


# --- _jaccard ---

def test_jaccard_identical():
    assert patterns._jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint():
    assert patterns._jaccard({"a"}, {"b"}) == 0.0


def test_jaccard_both_empty():
    assert patterns._jaccard(set(), set()) == 0.0


def test_jaccard_partial():
    assert patterns._jaccard({"a", "b"}, {"a", "c"}) == 1 / 3


# --- detect() semantic clustering ---

def _insert(spine, user, query, lane="chat"):
    with spine.write() as c:
        c.execute(
            "INSERT INTO interactions(user, query, lane) VALUES (?,?,?)",
            (user, query, lane),
        )


def test_detect_clusters_differently_worded_queries(spine):
    # Exact-normalized-form matching (the old behaviour) would treat these as
    # 5 distinct one-off queries. Keyword-jaccard clustering groups them by
    # shared domain keywords {prirez, split} instead.
    queries = [
        "koliki je prirez za Split",
        "prirez za Split koliki je",
        "prirez Split",
        "koji prirez vrijedi za Split",
        "kolika stopa prirez Split",
    ]
    for q in queries:
        _insert(spine, "ana", q)
    groups = patterns.detect(spine, min_count=5)
    assert len(groups) == 1
    assert groups[0]["count"] >= 5
    rows = spine.read().execute("SELECT * FROM skill_suggestions").fetchall()
    assert len(rows) == 1
    assert rows[0]["count"] >= 5


def test_detect_four_similar_no_suggestion(spine):
    queries = [
        "koliki je prirez za Split",
        "prirez za Split koliki je",
        "prirez Split",
        "koji prirez vrijedi za Split",
    ]
    for q in queries:
        _insert(spine, "ana", q)
    groups = patterns.detect(spine, min_count=5)
    assert groups == []
    rows = spine.read().execute("SELECT * FROM skill_suggestions").fetchall()
    assert rows == []


def test_detect_does_not_merge_different_topics(spine):
    prirez_qs = [
        "koliki je prirez za Split",
        "prirez za Split koliki je",
        "prirez Split",
        "koji prirez vrijedi za Split",
        "kolika stopa prirez Split",
    ]
    pdv_qs = [
        "kolika je stopa pdv-a",
        "stopa pdv-a je kolika",
        "pdv stopa",
        "kolika je stopa pdv-a za usluge",
        "kolika stopa pdv-a vrijedi",
    ]
    for q in prirez_qs + pdv_qs:
        _insert(spine, "ana", q)
    groups = patterns.detect(spine, min_count=5)
    assert len(groups) == 2
    for g in groups:
        assert g["count"] >= 5
