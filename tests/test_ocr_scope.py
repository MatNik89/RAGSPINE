import os

import pytest

from ragspine.config import Config
from ragspine.docs import ocr


def _cfg(tmp_path, roots):
    old = dict(os.environ)
    os.environ.update({"RAGSPINE_DATA_DIR": str(tmp_path / "d"),
                       "RAGSPINE_MOUNT_ROOTS": ",".join(roots)})
    try:
        return Config.from_env()
    finally:
        os.environ.clear(); os.environ.update(old)


def test_scope_allows_mount_root(tmp_path):
    share = tmp_path / "share"; (share / "a").mkdir(parents=True)
    f = share / "a" / "x.pdf"; f.write_bytes(b"%PDF")
    cfg = _cfg(tmp_path, [str(share)])
    assert ocr.resolve_scoped_path(cfg, str(f)) == os.path.realpath(str(f))


def test_scope_rejects_outside(tmp_path):
    share = tmp_path / "share"; share.mkdir()
    outside = tmp_path / "other.pdf"; outside.write_bytes(b"%PDF")
    cfg = _cfg(tmp_path, [str(share)])
    with pytest.raises(ValueError):
        ocr.resolve_scoped_path(cfg, str(outside))
