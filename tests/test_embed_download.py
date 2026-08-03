"""ragspine setup --download-models warmup: mockan (bez pravog network downloada)."""
from ragspine.rag import embed


def test_download_model_reports_unavailable_without_deps(monkeypatch):
    monkeypatch.setattr(embed, "available", lambda: False)
    res = embed.download_model()
    assert res["ok"] is False and "fastembed" in res["error"]


def test_download_model_success_path(monkeypatch, cfg):
    monkeypatch.setattr(embed, "available", lambda: True)

    class _FakeTE:
        def __init__(self, model, local_files_only=False):
            assert local_files_only is False  # download MORA biti dozvoljen
            self.model = model

        def embed(self, texts):
            return iter([[0.0] * 1024 for _ in texts])

    import fastembed
    monkeypatch.setattr(fastembed, "TextEmbedding", _FakeTE, raising=False)
    embed._model = None
    embed._model_failed = False
    res = embed.download_model()
    assert res["ok"] is True and res["dim"] == 1024


def test_download_model_reports_error(monkeypatch, cfg):
    monkeypatch.setattr(embed, "available", lambda: True)

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("nema mreže")

    import fastembed
    monkeypatch.setattr(fastembed, "TextEmbedding", _Boom, raising=False)
    embed._model = None
    embed._model_failed = False
    res = embed.download_model()
    assert res["ok"] is False and "nema mreže" in res["error"]
