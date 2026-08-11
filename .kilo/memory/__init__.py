"""Kilo Memory — hybrid semantic memory for Kilo Code.

Hybrid semantic memory: SQLite WAL + fastembed vectors + FTS5 keyword + FSRS-lite decay + lossless archive.
SQLite-backed, local-only, zero API keys. 5 scopes: project | task | user | lesson | corpus.

Quick start:
    from .kilo.memory import get_repo
    repo = get_repo()
    repo.remember("FastAPI runs on port 8400", source="kilo", scope="project")
    results = repo.query("which port does FastAPI use?")

Usage from CLI:
    python3 -m kilo.memory remember "FastAPI runs on port 8400" --scope project
    python3 -m kilo.memory recall "which port" --scope project
    python3 -m kilo.memory stats
"""

try:
    from .memory import MemoryRepo, MemoryEntry, SCOPES
    from .store import MEMORY_PATH
except ImportError:
    from memory import MemoryRepo, MemoryEntry, SCOPES
    from store import MEMORY_PATH

_repo = None


def get_repo(path=None) -> MemoryRepo:
    global _repo
    if _repo is None or path is not None:
        _repo = MemoryRepo(path or MEMORY_PATH)
    return _repo


def remember(content: str, source: str = "kilo", scope: str = "project",
             confidence: float = 1.0) -> MemoryEntry:
    return get_repo().remember(content, source, scope, confidence)


def recall(query: str, scope: str = "project", limit: int = 10,
           deep: bool = False) -> list[MemoryEntry]:
    return get_repo().query(scope, query, limit, deep)


def stats() -> dict:
    return get_repo().stats()


def consolidate() -> int:
    return get_repo().consolidate()


def forget(scope: str, content: str):
    get_repo().forget(scope, content)


def clear():
    get_repo().clear_project()


__all__ = ["get_repo", "remember", "recall", "stats", "consolidate", "forget", "clear",
           "MemoryRepo", "MemoryEntry", "SCOPES", "MEMORY_PATH"]

if __name__ == "__main__":
    import sys
    repo = get_repo()
    args = sys.argv[1:]

    if not args or args[0] == "stats":
        st = repo.stats()
        print(f"Memory: {st['total_hot']} hot / {st['total_cold']} cold")
        for s in SCOPES:
            print(f"  {s}: {st['hot'].get(s, 0)} hot / {st['cold'].get(s, 0)} cold")

    elif args[0] == "remember" and len(args) >= 2:
        scope, conf = "project", 1.0
        a = args[1:]
        for i, x in enumerate(a):
            if x == "--scope" and i + 1 < len(a):
                scope, a = a[i + 1], a[:i] + a[i + 2:]
                break
        content = " ".join(a)
        e = repo.remember(content, source="cli", scope=scope, confidence=conf)
        print(f"[ok] saved to {scope}: {e.content[:80]}")

    elif args[0] == "recall" and len(args) >= 2:
        scope, limit, deep = "project", 10, False
        a = args[1:]
        while a and a[0].startswith("--"):
            if a[0] == "--scope" and len(a) > 1:
                scope, a = a[1], a[2:]
            elif a[0] == "--limit" and len(a) > 1:
                limit, a = int(a[1]), a[2:]
            elif a[0] == "--deep":
                deep, a = True, a[1:]
            else:
                break
        query = " ".join(a)
        results = repo.query(scope, query, limit, deep)
        for i, e in enumerate(results, 1):
            d = e.date[:10] if e.date else "?"
            print(f"  [{d}] [{e.confidence:.2f}] {e.content[:120]}")

    elif args[0] == "consolidate":
        n = repo.consolidate()
        print(f"[ok] archived {n} faded memories")

    elif args[0] == "forget" and len(args) >= 3:
        repo.forget(args[1], " ".join(args[2:]))
        print(f"[ok] forgotten")

    elif args[0] == "clear":
        repo.clear_project()
        print("[ok] project memory cleared")

    else:
        print(__doc__)
