# SOP (Standard Operating Procedure) editorial workflow: internal knowledge
# ("how we do X for client Y") goes draft -> submitted -> approved/rejected.
# An approved SOP is ingested into the RAG corpus (doc_type='sop') and thus
# becomes searchable/citable (authority.detect_authority recognizes a "SOP:"
# title as interna_procedura).
from atlas.business import sop_images
from atlas.docs.ingest import ingest_text

STATUSES = ("draft", "submitted", "approved", "rejected")
EDITABLE_STATUSES = ("draft", "rejected")
SUBMITTABLE_STATUSES = ("draft", "rejected")

SOP_TEMPLATE = """# {title}

## Klijent
{client}

## Kategorija
{category}

## Postupak / koraci
{procedure}

## Alati
{tools}

## Česte greške
{mistakes}

## Izvor/referenca
{source}
"""


def new_sop_content(title: str, category: str, procedure: str = "", tools: str = "",
                     mistakes: str = "", source: str = "") -> str:
    return SOP_TEMPLATE.format(
        title=title, client="-", category=category,
        procedure=procedure or "-", tools=tools or "-",
        mistakes=mistakes or "-", source=source or "-",
    )


def _get_row(spine, sop_id: int):
    return spine.read().execute("SELECT * FROM sop_pages WHERE id=?", (sop_id,)).fetchone()


def _require_row(spine, sop_id: int):
    row = _get_row(spine, sop_id)
    if row is None:
        raise ValueError(f"nepoznat SOP: {sop_id}")
    return row


def create_sop(spine, author: str, title: str, category: str, content: str,
                client_id: int | None = None) -> int:
    with spine.write() as c:
        sop_id = c.execute(
            "INSERT INTO sop_pages(title,client_id,category,content,status,author) "
            "VALUES(?,?,?,?,'draft',?)",
            (title, client_id, category, content, author),
        ).lastrowid
    spine.audit(author, "sop_create", f"sop:{sop_id}", title)
    return sop_id


def submit_draft(spine, sop_id: int, author: str) -> None:
    row = _require_row(spine, sop_id)
    if row["status"] not in SUBMITTABLE_STATUSES:
        raise ValueError(f"SOP {sop_id} nije draft/rejected (status={row['status']!r})")
    with spine.write() as c:
        c.execute(
            "UPDATE sop_pages SET status='submitted', updated_at=datetime('now') WHERE id=?",
            (sop_id,),
        )
    action = "sop_resubmit" if row["status"] == "rejected" else "sop_submit"
    spine.audit(author, action, f"sop:{sop_id}")


def approve_draft(spine, sop_id: int, reviewer: str) -> int | None:
    row = _require_row(spine, sop_id)
    if row["status"] != "submitted":
        raise ValueError(f"SOP {sop_id} nije submitted (status={row['status']!r})")
    content = row["content"]
    # ponytail: image OCR text is fetched best-effort — a broken images table
    # or a stray exception must never block SOP approval.
    try:
        images = sop_images.list_images(spine, sop_id)
        img_text = "\n\n".join(img["ocr_text"] for img in images if img["ocr_text"])
        if img_text:
            content = f"{content}\n\n## Slike (OCR)\n{img_text}"
    except Exception:
        pass

    # ponytail: ingest + status UPDATE aren't in one transaction — if the UPDATE
    # failed after a successful ingest, an orphan corpus doc could remain.
    # Acceptable fail-closed (worst case: unreferenced searchable doc, no data
    # loss); upgrade path is wrapping both in a single spine.write() block.
    doc_id = ingest_text(spine, content, title=f"SOP: {row['title']}",
                          doc_type="sop", client_id=row["client_id"])
    with spine.write() as c:
        c.execute(
            "UPDATE sop_pages SET status='approved', reviewer=?, updated_at=datetime('now') WHERE id=?",
            (reviewer, sop_id),
        )
    spine.audit(reviewer, "sop_approve", f"sop:{sop_id}", f"doc_id:{doc_id}")
    return doc_id


def reject_draft(spine, sop_id: int, reviewer: str, reason: str = "") -> None:
    row = _require_row(spine, sop_id)
    if row["status"] != "submitted":
        raise ValueError(f"SOP {sop_id} nije submitted (status={row['status']!r})")
    with spine.write() as c:
        c.execute(
            "UPDATE sop_pages SET status='rejected', reviewer=?, updated_at=datetime('now') WHERE id=?",
            (reviewer, sop_id),
        )
    spine.audit(reviewer, "sop_reject", f"sop:{sop_id}", reason)


def list_pending(spine) -> list[dict]:
    rows = spine.read().execute(
        "SELECT * FROM sop_pages WHERE status='submitted' ORDER BY created_at"
    ).fetchall()
    return [dict(r) for r in rows]


def get_sop(spine, sop_id: int) -> dict | None:
    row = _get_row(spine, sop_id)
    return dict(row) if row is not None else None


def editorial_summary(spine) -> str:
    pending = list_pending(spine)
    if not pending:
        return "Nema SOP-ova na čekanju pregleda."
    titles = ", ".join(p["title"] for p in pending)
    return f"{len(pending)} SOP-a čeka pregled: {titles}."


def update_draft(spine, sop_id: int, author: str, content: str) -> None:
    row = _require_row(spine, sop_id)
    if row["status"] not in EDITABLE_STATUSES:
        raise ValueError(f"SOP {sop_id} nije uređiv (status={row['status']!r})")
    with spine.write() as c:
        c.execute(
            "UPDATE sop_pages SET content=?, base_version=base_version+1, "
            "updated_at=datetime('now') WHERE id=?",
            (content, sop_id),
        )
    spine.audit(author, "sop_update", f"sop:{sop_id}")
