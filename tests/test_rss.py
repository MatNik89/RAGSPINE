from atlas.web import watchlist as w

RSS_OK = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Narodne novine</title>
<item>
  <title>Zakon o ugostiteljskoj djelatnosti</title>
  <link>https://narodne-novine.nn.hr/clanci/1</link>
  <description>Novi zakon uredjuje ugostiteljstvo i pruzanje usluga hrane i pica.</description>
  <pubDate>Mon, 01 Jan 2027 00:00:00 GMT</pubDate>
</item>
<item>
  <title>Pravilnik o šumarstvu</title>
  <link>https://narodne-novine.nn.hr/clanci/2</link>
  <description>Pravilnik o gospodarenju sumama.</description>
  <pubDate>Mon, 01 Jan 2027 00:00:00 GMT</pubDate>
</item>
</channel></rss>""".encode("utf-8")

RSS_BAD = b"<rss><channel><item><title>unclosed"


def test_parse_rss_two_items():
    items = w.parse_rss(RSS_OK)
    assert len(items) == 2
    assert items[0]["title"] == "Zakon o ugostiteljskoj djelatnosti"
    assert items[0]["link"] == "https://narodne-novine.nn.hr/clanci/1"
    assert items[1]["title"] == "Pravilnik o šumarstvu"
    assert items[1]["link"] == "https://narodne-novine.nn.hr/clanci/2"


def test_parse_rss_malformed_returns_empty():
    assert w.parse_rss(RSS_BAD) == []
    assert w.parse_rss(b"not xml at all") == []


def test_check_rss_matches_client_industry_only(spine, cfg):
    sid = w.add_source(spine, "https://narodne-novine.nn.hr/rss.aspx?tip=1", kind="rss")
    with spine.write() as c:
        c.execute(
            "INSERT INTO clients(name,oib,industry) VALUES(?,?,?)",
            ("Konoba d.o.o.", "11111111111", "ugostiteljstvo"),
        )

    results = w.check_rss(spine, cfg, fetch=lambda u, **k: RSS_OK)
    assert len(results) == 1
    assert "ugostitelj" in results[0]["title"].lower()

    notif = spine.read().execute(
        "SELECT COUNT(*) FROM notifications WHERE kind='rss'"
    ).fetchone()[0]
    assert notif == 1

    # second run over the same feed: nothing new (seen-set dedup)
    results2 = w.check_rss(spine, cfg, fetch=lambda u, **k: RSS_OK)
    assert results2 == []
    notif2 = spine.read().execute(
        "SELECT COUNT(*) FROM notifications WHERE kind='rss'"
    ).fetchone()[0]
    assert notif2 == 1
