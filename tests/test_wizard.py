from ragspine.ops import wizard


def test_render_preflight_blocks_on_fail():
    reqs = [
        {"key": "python", "naziv": "Python", "status": "ok", "detalj": "3.11", "fix": ""},
        {"key": "disk", "naziv": "Disk", "status": "fail", "detalj": "0 GB", "fix": "oslobodi"},
    ]
    lines = []
    ok = wizard.render_preflight(reqs, out=lines.append)
    assert ok is False
    assert any("✗" in l for l in lines)
    assert any("oslobodi" in l for l in lines)   # fix se prikaže za fail


def test_render_preflight_passes_when_no_fail():
    reqs = [
        {"key": "python", "naziv": "Python", "status": "ok", "detalj": "3.11", "fix": ""},
        {"key": "internet", "naziv": "Internet", "status": "warn", "detalj": "nema", "fix": "spoji"},
    ]
    assert wizard.render_preflight(reqs, out=lambda *_: None) is True
