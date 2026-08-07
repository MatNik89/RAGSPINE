"""model_table: disk procjena, rangirane namjene, poravnanje tablice."""
from atlas.ops import model_table as mt


def test_disk_gb_q4_7b():
    # 7B × 4.6 bita / 8 × 1.08 ≈ 4.3 GB
    assert 3.5 < mt.disk_gb("7B", "Q4_K_M") < 5.0


def test_disk_gb_q2_manji_od_q8():
    assert mt.disk_gb("7B", "Q2_K") < mt.disk_gb("7B", "Q8_0")


def test_disk_gb_milijuni_parametara():
    # 135M model — disk ispod pola GB
    assert 0 < mt.disk_gb("135M", "Q4_K_M") < 0.5


def test_disk_gb_nepoznato_vraca_nulu():
    assert mt.disk_gb("", "Q4_K_M") == 0.0
    assert mt.disk_gb("7B", "") == 0.0
    assert mt.disk_gb("čudno", "Q4") == 0.0


def test_namjene_rangirane_po_obitelji():
    assert mt.namjene("qwen2.5:7b").startswith("chat")
    assert "›" in mt.namjene("qwen2.5:7b")
    assert mt.namjene("deepseek-r1:7b").startswith("reasoning")
    assert mt.namjene("qwen2.5-coder:7b").startswith("kod")


def test_namjene_fallback_na_use_case():
    assert mt.namjene("nepoznati-model:1b", "brzi asistent") == "brzi asistent"
    assert mt.namjene("nepoznati-model:1b", "") == "chat"


def test_table_rows_poravnanje_i_zvjezdica():
    rows = [
        {"ollama_name": "qwen2.5:7b", "params": "7B", "best_quant": "Q4_K_M",
         "memory_gb": 5.2, "tps": 11.0, "fit_label": "Good", "use_case": ""},
        {"ollama_name": "phi3:mini", "params": "3.8B", "best_quant": "Q4_K_M",
         "memory_gb": 3.1, "tps": 18.0, "fit_label": "Marginal", "use_case": ""},
    ]
    header, lines = mt.table_rows(rows)
    assert len(lines) == 2
    for col in ("Naziv", "Param", "Kvant", "RAM", "Disk", "Brzina", "Namjena"):
        assert col in header
    assert "⭐" in lines[0] and "⭐" not in lines[1]
    assert "🟢" in lines[0] and "🟡" in lines[1]
    assert "~5.2 GB" in lines[0].replace(",", ".")
    # Disk stupac popunjen procjenom, ne '?'
    assert lines[0].count("GB") >= 2


def test_table_rows_prazno():
    header, lines = mt.table_rows([])
    assert lines == [] and "Naziv" in header
