"""Installer server-first hook: /health mora potvrditi da je ovo ATLAS server
(atlas=true), verziju i je li setup gotov — radna stanica se veže tek onda."""
from fastapi.testclient import TestClient

import atlas
from atlas.ops import wizard_state
from atlas.web.api import create_app


def test_health_marks_atlas_server_public(spine, cfg):
    c = TestClient(create_app(spine, cfg))
    r = c.get("/health")  # bez autha (installer ga zove izvana)
    assert r.status_code == 200
    j = r.json()
    assert j["atlas"] is True and j["version"] == atlas.__version__
    assert j["setup_complete"] is False  # svjež server -> nije još postavljen


def test_health_setup_complete_flips(spine, cfg):
    wizard_state.mark_complete(spine)
    c = TestClient(create_app(spine, cfg))
    assert c.get("/health").json()["setup_complete"] is True


def test_version_matches_pyproject():
    import pathlib
    import re
    txt = pathlib.Path(__file__).resolve().parent.parent.joinpath("pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', txt, re.MULTILINE)
    assert m and m.group(1) == atlas.__version__  # __version__ usklađen s pyproject
