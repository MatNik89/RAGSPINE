"""Fill PDF AcroForm widgets from DB data."""
from atlas.core import optional


class FormUnavailable(Exception):
    pass


def _fitz():
    fitz = optional.need("fitz", "PDF forms")
    if fitz is None:
        raise FormUnavailable("PyMuPDF (fitz) nije instaliran")
    return fitz


def fill(path: str, fields: dict, out_path: str) -> int:
    fitz = _fitz()
    doc = fitz.open(path)
    try:
        count = 0
        for page in doc:
            for widget in page.widgets() or []:
                if widget.field_name in fields:
                    widget.field_value = str(fields[widget.field_name])
                    widget.update()
                    count += 1
        doc.save(out_path)
        return count
    finally:
        doc.close()


def client_fields(spine, client_id) -> dict:
    row = spine.read().execute(
        "SELECT name, oib, email, phone, owner FROM clients WHERE id=?", (client_id,)
    ).fetchone()
    if not row:
        return {}
    return {
        "naziv": row["name"],
        "OIB": row["oib"],
        "email": row["email"],
        "telefon": row["phone"],
        "vlasnik": row["owner"],
    }
