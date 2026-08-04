"""GDPR sweep: delete every row across user-data tables matching a search term."""
import hashlib
import os
import sqlite3

from ragspine.core import security

# ponytail: LIKE substring match is O(rows) full scan, no index — fine at this
# scale (single-tenant SQLite, sweep is rare/manual). Upgrade path: add
# indexes on hot columns if forget() ever runs on a hot path.
SIMPLE_TABLES = {
    "clients": ("name", "oib", "email", "phone", "owner"),
    "notes": ("body", "author"),
    "eracuni": ("supplier_oib", "customer_oib", "raw_path"),
    "interactions": ("query", "answer", "user"),
    "knowledge": ("question", "answer", "tags"),
    "memory": ("key", "value"),
    "expiry_items": ("label",),
    "audit_log": ("detail", "entity", "user"),
    "notifications": ("body",),
    "reminders": ("body", "user"),
    "feedback": ("query", "comment"),
    # razgovorna memorija + kesirani odgovori + log poruka nose klijentski PII
    # koji je ranije preživljavao forget (Codex/red-team nalaz)
    "mem_l0": ("content", "distilled"),
    "mem_l1": ("content",),
    "mem_l3": ("persona",),
    "query_cache": ("query", "answer", "meta"),
    "message_log": ("subject", "body_preview"),
}

# subquery predicates (no Python-side id lists — sidesteps SQLite's ~999 bound
# param ceiling entirely instead of batching IN(...) lists).
_DOC_WHERE = "title LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\' OR source_url LIKE ? ESCAPE '\\'"
_NODE_WHERE = "value LIKE ? ESCAPE '\\'"


def _escape_pattern(term: str) -> str:
    """Build a LIKE pattern with %, _ and the escape char itself escaped, so a
    term containing literal % or _ is matched verbatim instead of acting as a
    wildcard (which would over-match and DELETE unrelated rows)."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _count(c, table, cols, pattern):
    where = " OR ".join(f"{col} LIKE ? ESCAPE '\\'" for col in cols)
    return c.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", [pattern] * len(cols)).fetchone()[0]


def _delete(c, table, cols, pattern):
    where = " OR ".join(f"{col} LIKE ? ESCAPE '\\'" for col in cols)
    c.execute(f"DELETE FROM {table} WHERE {where}", [pattern] * len(cols))


_ERACUN_MATCH = ("supplier_oib LIKE ? ESCAPE '\\' OR customer_oib LIKE ? ESCAPE '\\' "
                 "OR raw_path LIKE ? ESCAPE '\\'")


def _resolved_if_safe(path: str, canon_roots: list[str]) -> str | None:
    """Vrati kanonski realpath datoteke SAMO ako je sigurna za brisanje: nije
    symlink, realpath leži pod kanonskim korijenom, obična je datoteka (ne mapa).
    Codex nalaz: goli root-check nije dovoljan — symlink/ne-regularni čvor bi
    izveo brisanje izvan namjere. Vraća baš taj realpath da unlink ne re-resolva
    (uži TOCTOU prozor)."""
    if not path or os.path.islink(path):
        return None
    rp = os.path.realpath(path)
    if not any(security.path_under(rp, root) for root in canon_roots):
        return None
    return rp if os.path.isfile(rp) else None


def _collect_file_paths(c, pattern: str) -> set:
    paths = set()
    for row in c.execute(f"SELECT path FROM documents WHERE {_DOC_WHERE}", (pattern,) * 3):
        if row["path"]:
            paths.add(row["path"])
    for row in c.execute(f"SELECT raw_path FROM eracuni WHERE {_ERACUN_MATCH}", (pattern,) * 3):
        if row["raw_path"]:
            paths.add(row["raw_path"])
    return paths


def _survivor_realpaths(c, pattern: str) -> set:
    """Kanonski realpath-evi datoteka koje drže retci koji NE odgovaraju terminu
    (preživjeli). Datoteka koju drži preživjeli red ne smije biti obrisana, čak
    ni kad je dijeli neki obrisani red (dijeljen path / alias). Realpath usporedba
    hvata /x/a vs /x/./a vs symlink-alias istog fajla (Codex nalaz)."""
    # COALESCE nužan: NOT (col LIKE ?) je NULL (ne TRUE) kad je col NULL — bez
    # njega bi red s NULL source_url/oib ispao iz skupa preživjelih i njegov bi
    # dijeljeni fajl bio pogrešno obrisan (SQL three-valued logic zamka).
    doc_match = ("COALESCE(title,'') LIKE ? ESCAPE '\\' OR COALESCE(path,'') LIKE ? ESCAPE '\\' "
                 "OR COALESCE(source_url,'') LIKE ? ESCAPE '\\'")
    er_match = ("COALESCE(supplier_oib,'') LIKE ? ESCAPE '\\' OR COALESCE(customer_oib,'') LIKE ? "
                "ESCAPE '\\' OR COALESCE(raw_path,'') LIKE ? ESCAPE '\\'")
    keep = set()
    for row in c.execute(
            f"SELECT path FROM documents WHERE path IS NOT NULL AND NOT ({doc_match})", (pattern,) * 3):
        if row["path"]:
            keep.add(os.path.realpath(row["path"]))
    for row in c.execute(
            f"SELECT raw_path FROM eracuni WHERE raw_path IS NOT NULL AND NOT ({er_match})",
            (pattern,) * 3):
        if row["raw_path"]:
            keep.add(os.path.realpath(row["raw_path"]))
    return keep


def forget(spine, term: str, dry: bool = False, cfg=None) -> dict:
    """Obriši svaki red s PII-em koji odgovara terminu. Ako je `cfg` dan, dodatno
    (samo izvan dry moda) unlinka izvorne datoteke skenova/e-računa pod kanonskim
    korijenima — inače PII PDF-ovi prežive brisanje DB retka (GDPR nepotpunost)."""
    pattern = _escape_pattern(term)
    result: dict[str, int] = {}
    file_paths: set = set()
    survivors: set = set()

    with spine.write() as c:
        if cfg is not None:
            file_paths = _collect_file_paths(c, pattern)
            survivors = _survivor_realpaths(c, pattern)
        result["documents"] = c.execute(
            f"SELECT COUNT(*) FROM documents WHERE {_DOC_WHERE}", (pattern,) * 3).fetchone()[0]
        result["chunks"] = c.execute(
            f"SELECT COUNT(*) FROM chunks WHERE doc_id IN (SELECT id FROM documents WHERE {_DOC_WHERE})",
            (pattern,) * 3).fetchone()[0]
        # doc_extracts nosi ekstrahirani PII (broj osobne...) — briše se i po
        # pripadnosti dokumentu i po sadržaju fields_json (GDPR forget)
        _extract_where = (f"doc_id IN (SELECT id FROM documents WHERE {_DOC_WHERE}) "
                          "OR fields_json LIKE ? ESCAPE '\\'")
        result["doc_extracts"] = c.execute(
            f"SELECT COUNT(*) FROM doc_extracts WHERE {_extract_where}",
            (pattern,) * 4).fetchone()[0]
        if not dry:
            c.execute(f"DELETE FROM doc_extracts WHERE {_extract_where}", (pattern,) * 4)
        if not dry:
            # embeddings su izvedeni iz PII teksta — obriši ih prije chunkova;
            # vec_chunks je vec0 virtualna tablica koja možda ne postoji dok
            # embedding model nije aktiviran.
            try:
                c.execute(
                    "DELETE FROM vec_chunks WHERE chunk_id IN "
                    f"(SELECT id FROM chunks WHERE doc_id IN (SELECT id FROM documents WHERE {_DOC_WHERE}))",
                    (pattern,) * 3)
            except sqlite3.OperationalError:
                pass
            # chunks before documents: keeps chunks_fts trigger-consistent and
            # avoids orphaning chunks under a still-matching-but-not-yet-
            # deleted parent mid-sweep.
            c.execute(
                f"DELETE FROM chunks WHERE doc_id IN (SELECT id FROM documents WHERE {_DOC_WHERE})",
                (pattern,) * 3)
            c.execute(f"DELETE FROM documents WHERE {_DOC_WHERE}", (pattern,) * 3)

        result["kg_nodes"] = c.execute(
            f"SELECT COUNT(*) FROM kg_nodes WHERE {_NODE_WHERE}", (pattern,)).fetchone()[0]
        result["kg_edges"] = c.execute(
            f"SELECT COUNT(*) FROM kg_edges WHERE src IN (SELECT id FROM kg_nodes WHERE {_NODE_WHERE}) "
            f"OR dst IN (SELECT id FROM kg_nodes WHERE {_NODE_WHERE})",
            (pattern, pattern)).fetchone()[0]
        if not dry:
            c.execute(
                f"DELETE FROM kg_edges WHERE src IN (SELECT id FROM kg_nodes WHERE {_NODE_WHERE}) "
                f"OR dst IN (SELECT id FROM kg_nodes WHERE {_NODE_WHERE})",
                (pattern, pattern))
            c.execute(f"DELETE FROM kg_nodes WHERE {_NODE_WHERE}", (pattern,))

        for table, cols in SIMPLE_TABLES.items():
            n = _count(c, table, cols, pattern)
            result[table] = n
            if not dry and n:
                _delete(c, table, cols, pattern)

        if not dry:
            # Proof-of-erasure row, written AFTER the sweep so it can't be
            # swept by its own audit_log match, and redacted (hash, not the
            # raw term) so the erased PII isn't reintroduced into the DB by
            # the very row that proves it was erased.
            digest = hashlib.sha256(term.encode()).hexdigest()[:16]
            total = sum(result.values())
            c.execute(
                "INSERT INTO audit_log(user,action,entity,detail) VALUES(?,?,?,?)",
                ("system", "gdpr_forget", "gdpr_sweep", f"gdpr_forget hash={digest} rows={total}"))

    if cfg is not None:
        # unlink izvornih datoteka NAKON što su DB retci obrisani (i commitani):
        # tako pad usred posla ne ostavlja DB koji tvrdi da fajl postoji dok je
        # već obrisan. Preskoči datoteku koju drži preživjeli (ne-podudarni) red.
        # `survivors` je snimljen prije brisanja pa dry-preview i stvarni prolaz
        # daju isti broj (Codex: dry je prije uvijek vidio vlastite reference → 0).
        canon_roots = [os.path.realpath(r) for r in
                       ([cfg.data_dir, cfg.nas_root] + list(cfg.mount_roots)) if r]
        removed = 0
        for p in file_paths:
            rp = _resolved_if_safe(p, canon_roots)
            if rp is None or rp in survivors:
                continue
            if not dry:
                try:
                    os.unlink(rp)
                except OSError:
                    continue
            removed += 1
        result["files"] = removed

    if not dry:
        with spine.write() as c:
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    return result
