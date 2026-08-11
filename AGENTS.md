# AGENTS.md — Kilo Code project instructions

## Kilo Memory — hybrid recall

I have persistent memory at `~/.kilo/kilo_memory.db`:
- **Storage:** SQLite WAL spine + fastembed BGE-small vectors + FTS5 keyword index
- **Scopes:** project | task | user | lesson | corpus
- **Retrieval:** hybrid (keyword ∪ vector → RRF fuse) + FSRS-lite decay + associate expansion
- **Archive:** lossless cold tier (never deletes — `deep=True` includes archived)

Modules: `.kilo/memory/store.py`, `memory.py`, `memvec.py`, `fts.py`, `recall.py`, `archive.py`, `embed.py`

### On session start — recall project context
```bash
PYTHONPATH=.kilo python3 -c "
import sys; sys.path.insert(0,'.kilo'); from memory import get_repo
repo = get_repo()
for e in repo.query('project', '<current task description>', limit=5):
    print(f'[{e.date[:10]}] {e.content[:120]}')"
```

### During session — save important facts
```bash
PYTHONPATH=.kilo python3 -c "
import sys; sys.path.insert(0,'.kilo'); from memory import get_repo
get_repo().remember('<exact fact>', source='kilo', scope='<project|lesson|task|user>')"
```

### Slash commands
- `/memory recall "<query>"` — search memory
- `/memory remember "<fact>"` — save to memory  
- `/memory stats` — show counts per scope
- `/memory consolidate` — archive faded memories
- `/memory forget <scope> "<content>"` — delete a memory
