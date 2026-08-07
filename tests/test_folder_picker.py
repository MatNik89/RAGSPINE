"""folder_picker: browsanje kroz numerirani fallback (bez TTY-ja)."""
import ntpath
import os

from atlas.ops import folder_picker as fp


def _reader(*answers):
    it = iter(answers)
    return lambda _="": next(it)


def test_roots_posix():
    if os.name == "nt":
        return
    roots = fp._roots()
    assert "/" in roots and os.path.expanduser("~") in roots


def test_subdirs_abecedno_bez_datoteka(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "a").mkdir()
    (tmp_path / "datoteka.txt").write_text("x")
    assert fp._subdirs(str(tmp_path)) == ["a", "b"]


def test_subdirs_nedostupno_prazno():
    assert fp._subdirs("/nema/takve/mape") == []


def test_pick_folder_odaberi_trenutnu(tmp_path, monkeypatch):
    """Korijen → uđi u podmapu → [✓ ODABERI OVU MAPU]."""
    (tmp_path / "klijenti").mkdir()
    monkeypatch.setattr(fp, "_roots", lambda: [str(tmp_path)])
    # početni ekran: 1=root, 2=Mrežna, 3=Ručni upis, 4=Odustani
    # u mapi: 1=[✓ ODABERI], 2=.., 3+=podmape
    got = fp.pick_folder(input_fn=_reader("1", "3", "1"), out=lambda *_: None)
    assert got == os.path.join(str(tmp_path), "klijenti")


def test_pick_folder_dvije_razine_pa_gore(tmp_path, monkeypatch):
    (tmp_path / "a" / "b").mkdir(parents=True)
    monkeypatch.setattr(fp, "_roots", lambda: [str(tmp_path)])
    # uđi u a, uđi u b, gore (..), odaberi a
    got = fp.pick_folder(input_fn=_reader("1", "3", "3", "2", "1"),
                         out=lambda *_: None)
    assert got == os.path.join(str(tmp_path), "a")


def test_pick_folder_odustani(monkeypatch):
    monkeypatch.setattr(fp, "_roots", lambda: ["C:\\"])
    assert fp.pick_folder(input_fn=_reader("4"), out=lambda *_: None) is None


def test_pick_folder_rucni_upis(monkeypatch, tmp_path):
    monkeypatch.setattr(fp, "_roots", lambda: [str(tmp_path)])
    got = fp.pick_folder(input_fn=_reader("3", r"\\nas\ured"), out=lambda *_: None)
    assert got == r"\\nas\ured"


def test_pick_folder_mrezna_lokacija(monkeypatch, tmp_path):
    """Mrežna opcija: upis \\\\server\\share pa browsanje od te točke —
    ovdje share ne postoji pa _subdirs vrati prazno; odaberi je odmah."""
    monkeypatch.setattr(fp, "_roots", lambda: [str(tmp_path)])
    got = fp.pick_folder(input_fn=_reader("2", r"\\nas\share", "1"),
                         out=lambda *_: None)
    assert got == r"\\nas\share"


def test_parent_ntpath_windows_root_izlaz():
    """C:\\ je vrh — ntpath.dirname("C:") == "C:" (drive-relativno, NE vrh
    stringa), pa _parent mora prepoznati vrh preko stripanog oblika."""
    assert fp._parent("C:\\", ntpath.dirname) is None


def test_parent_ntpath_unc_root_izlaz():
    assert fp._parent(r"\\nas\share", ntpath.dirname) is None


def test_parent_ntpath_podmapa():
    assert fp._parent(r"C:\Users\X", ntpath.dirname) == r"C:\Users"


def test_pick_folder_posix_vrh_izlaz_bez_lazi_ekrana(monkeypatch):
    """POSIX vrh: uđi u / pa .. mora izaći odmah (None), bez lažnog
    među-ekrana koji bi drive-relativni bug proizveo na Windowsu."""
    monkeypatch.setattr(fp, "_roots", lambda: ["/"])
    got = fp.pick_folder(input_fn=_reader("1", "2"), out=lambda *_: None)
    assert got is None


def test_pick_folder_status_u_naslovu(tmp_path, monkeypatch):
    """Naslov browsanja sadrži trenutnu putanju, header legendu tipki."""
    monkeypatch.setattr(fp, "_roots", lambda: [str(tmp_path)])
    lines = []
    fp.pick_folder(input_fn=_reader("1", "1"), out=lines.append)
    text = "\n".join(lines)
    assert str(tmp_path) in text
    assert "ODABERI OVU MAPU" in text
