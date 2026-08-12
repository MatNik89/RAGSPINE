"""UBL 2.1 e-invoice XML parser + OIB-based auto-sort to client NAS folder."""
import os
import shutil
import xml.etree.ElementTree as ET

from atlas.core import security, xmlsafe
from atlas.docs.ingest import ingest_text

NS = {
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
}


def _text(el, path):
    node = el.find(path, NS)
    return node.text.strip() if node is not None and node.text else None


def _oib(party_el):
    if party_el is None:
        return None
    raw = _text(party_el, "cac:Party/cac:PartyTaxScheme/cbc:CompanyID")
    if raw is None:
        return None
    raw = raw.strip().upper()
    return raw[2:] if raw.startswith("HR") else raw


def _float(s):
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_ubl(xml_bytes: bytes) -> dict:
    try:
        root = xmlsafe.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise ValueError(f"malformed UBL XML: {e}") from e

    supplier = root.find("cac:AccountingSupplierParty", NS)
    customer = root.find("cac:AccountingCustomerParty", NS)

    lines = []
    for line_el in root.findall("cac:InvoiceLine", NS):
        name = _text(line_el, "cac:Item/cbc:Name") or _text(line_el, "cbc:Note")
        qty = _float(_text(line_el, "cbc:InvoicedQuantity"))
        price = _float(_text(line_el, "cac:Price/cbc:PriceAmount"))
        lines.append({"name": name, "qty": qty, "price": price})

    return {
        "supplier_oib": _oib(supplier),
        "customer_oib": _oib(customer),
        "total": _float(_text(root, "cac:LegalMonetaryTotal/cbc:PayableAmount")),
        "vat": _float(_text(root, "cac:TaxTotal/cbc:TaxAmount")),
        "currency": _text(root, "cbc:DocumentCurrencyCode"),
        "issued": _text(root, "cbc:IssueDate"),
        "lines": lines,
    }


def store(spine, parsed: dict, raw_path: str = "") -> int:
    with spine.write() as c:
        eid = c.execute(
            """INSERT INTO eracuni(doc_id, supplier_oib, customer_oib, total, vat, currency, issued, raw_path)
               VALUES(?,?,?,?,?,?,?,?)""",
            (None, parsed.get("supplier_oib"), parsed.get("customer_oib"), parsed.get("total"),
             parsed.get("vat"), parsed.get("currency"), parsed.get("issued"), raw_path),
        ).lastrowid

    summary = (
        f"E-račun dobavljač OIB {parsed.get('supplier_oib')} kupac OIB {parsed.get('customer_oib')} "
        f"iznos {parsed.get('total')} {parsed.get('currency')} PDV {parsed.get('vat')} "
        f"datum {parsed.get('issued')}"
    )
    doc_id = ingest_text(spine, summary, title=f"E-račun {parsed.get('issued') or ''}".strip(),
                          doc_type="racun", path=raw_path)
    if doc_id is not None:
        with spine.write() as c:
            c.execute("UPDATE eracuni SET doc_id=? WHERE id=?", (doc_id, eid))
    return eid


def _resolve_dest(cfg, nas_folder: str) -> str:
    root = os.path.realpath(cfg.nas_root)
    dest = os.path.realpath(os.path.join(root, nas_folder))
    if not security.path_under(dest, root):
        raise ValueError(f"path traversal blocked: {nas_folder!r} escapes nas_root")
    return dest


def autosort(spine, cfg, xml_path: str, pdf_path: str | None = None) -> str | None:
    with open(xml_path, "rb") as f:
        parsed = parse_ubl(f.read())

    customer_oib = parsed.get("customer_oib")
    client = spine.read().execute(
        "SELECT * FROM clients WHERE oib=?", (customer_oib,)
    ).fetchone()

    if client is None:
        with spine.write() as c:
            c.execute(
                "INSERT INTO notifications(kind, body, client_id) VALUES(?,?,?)",
                ("eracun_unmatched", f"E-račun nepoznat kupac OIB {customer_oib}: {xml_path}", None),
            )
        return None

    # same two-root guard as all other consumers of nas_folder — a client in the
    # registered KLIJENTI folder (absolute nas_folder) would otherwise be rejected
    from atlas.business.onboarding import _client_dir
    dest_dir = _client_dir(spine, cfg, client["id"])
    os.makedirs(dest_dir, exist_ok=True)

    # collision guard: an e-invoice with an attachment name that already exists in
    # the folder must not silently overwrite the existing one (an attacker sends an
    # attachment "racun.xml" to destroy a previously filed one) — uniquify like
    # onboarding.add_document (stem_2.ext, ...).
    dest_xml = _uniquify(dest_dir, os.path.basename(xml_path))
    shutil.move(xml_path, dest_xml)
    if pdf_path:
        shutil.move(pdf_path, _uniquify(dest_dir, os.path.basename(pdf_path)))

    return dest_xml


def _uniquify(dest_dir: str, name: str) -> str:
    stem, ext = os.path.splitext(name)
    n = 1
    while os.path.exists(os.path.join(dest_dir, name)):
        n += 1
        name = f"{stem}_{n}{ext}"
    return os.path.join(dest_dir, name)
