"""winpath: čisti merge/lookup dijelovi; registry sloj je Windows-only guard."""
import os
import shutil

from atlas.ops import winpath


def test_merge_path_dedup_i_redoslijed():
    got = winpath._merge_path(
        os.pathsep.join(["C", "A"]),
        os.pathsep.join(["A", "B"]),
        os.pathsep.join(["B", "D"])
    )
    assert got == os.pathsep.join(["A", "B", "D", "C"])


def test_merge_path_case_insensitive_dedup():
    got = winpath._merge_path("", r"C:\Alat", os.pathsep.join([r"c:\alat", "X"]))
    assert got == os.pathsep.join([r"C:\Alat", "X"])


def test_merge_path_prazni_segmenti_ispadaju():
    assert winpath._merge_path(
        os.pathsep.join(["", ""]),
        os.pathsep.join(["A", "", "B"]),
        ""
    ) == os.pathsep.join(["A", "B"])


def test_find_binary_which_ima_prednost(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda k: "/usr/bin/tess")
    assert winpath.find_binary("tesseract") == "/usr/bin/tess"


def test_find_binary_poznata_lokacija(tmp_path, monkeypatch):
    exe = tmp_path / "tesseract.exe"
    exe.write_bytes(b"x")
    monkeypatch.setattr(shutil, "which", lambda k: None)
    monkeypatch.setitem(winpath.KNOWN_LOCATIONS, "tesseract", [str(exe)])
    assert winpath.find_binary("tesseract") == str(exe)


def test_find_binary_nema_nigdje(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda k: None)
    monkeypatch.setitem(winpath.KNOWN_LOCATIONS, "tesseract", ["/nema/toga"])
    assert winpath.find_binary("tesseract") is None
    assert winpath.find_binary("nepoznat-alat") is None


def test_registry_slojevi_su_noop_izvan_windowsa():
    if os.name == "nt":
        return   # na Windows CI ove funkcije stvarno diraju registry — preskoči
    assert winpath.refresh_path_from_registry() is False
    assert winpath.persist_user_env("X", "y") is False
    assert winpath.append_user_path("/tmp") is False
