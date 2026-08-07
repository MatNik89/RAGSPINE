"""Guards fix for: sql/graph/web lane handlers were dead at serve time because
LANE_HANDLERS is populated only by import side-effects, and the serve path
(__main__._cmd_serve -> web.api.create_app) never transitively imported the
sql_lane/graphrag/websearch modules — only "learn" registered via watchlist.

Runs in a subprocess with a fresh interpreter so no other test's imports can
mask the bug: importing only atlas.web.api must be enough to register all
four lane handlers, exactly as it needs to be at real `atlas serve` time.
"""
import subprocess
import sys


def test_all_lanes_registered_by_importing_web_api_alone():
    code = (
        "import atlas.web.api, atlas.rag.pipeline as p\n"
        "missing = {'sql', 'web', 'graph', 'learn'} - set(p.LANE_HANDLERS)\n"
        "assert not missing, f'lane handlers missing at serve time: {missing}'\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
