from atlas.web import learn as l

HTML = b"<html><body>Grad Sisak: stopa prireza 10%. Split 15%.</body></html>"


def test_learn_url_sets_overrides_ingests_and_audits(spine, cfg):
    result = l.learn_url(spine, cfg, "https://porezna.example/prirez", "ana",
                          fetch=lambda u, **k: HTML)
    assert result["overrides_set"]["Sisak"] == "10"
    assert result["overrides_set"]["Split"] == "15"
    assert spine.get_override("kalkulator", "prirez.Sisak") == "10"
    assert result["doc_id"] is not None
    row = spine.read().execute("SELECT * FROM audit_log WHERE action='learn_url'").fetchone()
    assert row is not None and row["user"] == "ana"


def test_clean_html_strips_script_and_style():
    html = b"<html><head><style>.x{}</style></head><body><script>alert(1)</script>Tekst ovdje.</body></html>"
    assert l.clean_html(html) == "Tekst ovdje."


def test_extract_city_rates_direct():
    assert l.extract_city_rates("Grad Sisak: stopa prireza 10%") == {"Sisak": "10"}
    assert l.extract_city_rates("Split 15%") == {"Split": "15"}


def test_hr_gradovi_is_union_of_both_lists():
    assert len(l.HR_GRADOVI) == 33
    assert "Zagreb" in l.HR_GRADOVI and "Makarska" in l.HR_GRADOVI
    assert "Gospić" in l.HR_GRADOVI and "Kutina" in l.HR_GRADOVI  # ex-watchlist-only


def _actor(role="member", username="ana"):
    from atlas.business.acl import Actor
    return Actor(user_id=1, org_id=1, role=role, username=username)


def test_lane_handler_with_url(spine, cfg):
    reply = l.handle(spine, cfg, "nauci s https://porezna.example/prirez", None,
                      fetch=lambda u, **k: HTML, actor=_actor())
    assert "porezna.example" in reply
    assert "Sisak" in reply and "10" in reply
    assert "Split" in reply and "15" in reply
    # audit nosi stvarnog korisnika, ne "sustav"
    row = spine.read().execute("SELECT user FROM audit_log WHERE action='learn_url'").fetchone()
    assert row["user"] == "ana"


def test_lane_handler_viewer_refused_no_side_effect(spine, cfg):
    # read-only viewer NE smije trovati korpus ni mijenjati prireze (MEDIUM fold)
    reply = l.handle(spine, cfg, "nauci s https://porezna.example/prirez", None,
                     fetch=lambda u, **k: HTML, actor=_actor(role="viewer"))
    assert "member" in reply
    assert spine.get_override("kalkulator", "prirez.Sisak") is None
    assert spine.read().execute("SELECT COUNT(*) n FROM documents").fetchone()["n"] == 0
    assert spine.read().execute("SELECT 1 FROM audit_log WHERE action='learn_url'").fetchone() is None


def test_lane_handler_without_url(spine, cfg):
    reply = l.handle(spine, cfg, "nauci nesto", None, actor=_actor())
    assert "URL" in reply or "https" in reply


def test_lane_handler_strips_trailing_sentence_punctuation(spine, cfg):
    seen_urls = []

    def fetch(u, **k):
        seen_urls.append(u)
        return HTML

    l.handle(spine, cfg, "nauci s https://porezna.example/prirez.", None, fetch=fetch,
             actor=_actor())
    assert seen_urls == ["https://porezna.example/prirez"]
