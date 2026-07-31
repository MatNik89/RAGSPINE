from ragspine.web import watchlist as w

HTML1 = b"<html><body>Prirez Split iznosi 10%. Stupa na snagu 1.1.2027.</body></html>"
HTML2 = b"<html><body>Prirez Split iznosi 12%. Stupa na snagu 1.1.2027.</body></html>"

def test_first_check_stores_no_change(spine, cfg):
    sid = w.add_source(spine, "https://porezna.example/prirez")
    ch = w.check_source(spine, cfg, w.get_source(spine, sid), fetch=lambda u, **k: HTML1)
    assert ch is None  # prvi fetch = baseline

def test_change_detected_and_override(spine, cfg):
    sid = w.add_source(spine, "https://porezna.example/prirez")
    w.check_source(spine, cfg, w.get_source(spine, sid), fetch=lambda u, **k: HTML1)
    ch = w.check_source(spine, cfg, w.get_source(spine, sid), fetch=lambda u, **k: HTML2)
    assert ch is not None and any("10" in d and "12" in d for d in ch.diff)
    assert spine.get_override("kalkulator", "prirez.Split") == "12"
    up = spine.read().execute("SELECT * FROM upcoming_changes").fetchone()
    assert up["effective_date"] == "2027-01-01"

def test_notification_created(spine, cfg):
    sid = w.add_source(spine, "https://x.example/z")
    w.check_source(spine, cfg, w.get_source(spine, sid), fetch=lambda u, **k: HTML1)
    w.check_source(spine, cfg, w.get_source(spine, sid), fetch=lambda u, **k: HTML2)
    assert spine.read().execute("SELECT COUNT(*) FROM notifications").fetchone()[0] >= 1
