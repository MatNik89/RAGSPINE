import json
import shutil
import subprocess
from pathlib import Path

EXT = Path(__file__).resolve().parent.parent / "extension"


def _manifest():
    return json.loads((EXT / "manifest.json").read_text())


def test_manifest_mv3_shape():
    m = _manifest()
    assert m["manifest_version"] == 3
    assert m["name"]
    assert m["background"]["service_worker"] == "background.js"
    assert m["action"]["default_popup"] == "popup.html"
    for perm in ("activeTab", "scripting", "storage", "tabs"):
        assert perm in m["permissions"]
    assert m["host_permissions"]


def test_referenced_files_exist_and_nonempty():
    for name in ("background.js", "popup.html", "popup.js"):
        p = EXT / name
        assert p.exists(), f"missing {name}"
        assert p.stat().st_size > 0, f"empty {name}"


def test_popup_html_references_popup_js():
    assert "popup.js" in (EXT / "popup.html").read_text()


def test_background_js_has_action_contract():
    src = (EXT / "background.js").read_text()
    for action in ("navigate", "click", "type", "screenshot", "read"):
        assert f'"{action}"' in src or f"'{action}'" in src, f"missing action {action}"
    assert "Authorization" in src and "Bearer" in src
    assert "/browser/cmd" in src
    assert "/browser/result" in src


def test_js_syntax_if_node_available():
    node = shutil.which("node")
    if not node:
        return
    for name in ("background.js", "popup.js"):
        r = subprocess.run([node, "--check", str(EXT / name)], capture_output=True, text=True)
        assert r.returncode == 0, f"{name} syntax error:\n{r.stderr}"
