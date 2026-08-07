import pytest

from atlas.rag.router import route


@pytest.mark.parametrize(
    "q,lane",
    [
        ("koliko računa imamo ovaj mjesec?", "sql"),
        ("zbroj PDV-a za srpanj", "sql"),
        ("top 5 klijenata po upitima", "sql"),
        ("nauči s https://porezna.hr/prirez", "learn"),
        ("pretraži web za novi zakon o radu", "web"),
        ("OCR-aj skenirane račune iz foldera", "ocr"),
        ("na koji konto knjižim reprezentaciju?", "knjizenje"),
        ("kakva je veza između klijenta X i dobavljača Y?", "graph"),
        ("bok", "no_retrieval"),
        ("obriši sve iz baze", "reject"),
        ("koliki je prirez za Split?", "chat"),
        ("što sve moram ovaj mjesec?", "chat"),
    ],
)
def test_route(q, lane):
    assert route(q) == lane
