from ragspine.rag.composer import compose
from ragspine.rag.retrieval import Hit


def test_compose_marks_sources():
    sys_p, msgs = compose("stopa pdv?", [Hit(1, 1, "pdv-stope", "Stopa je 25%.", 1.0, "zakon")])
    assert "[1] (ZAKON) pdv-stope" in msgs[-1]["content"]
    assert "ne znam" in sys_p.lower() or "Ne znam" in sys_p
    assert msgs[-1]["role"] == "user"
