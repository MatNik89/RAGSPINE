# T6 — Cjenik usluga (firm price list + per-client quote + market compare)

Status: DONE
Commit: (see below)
Full suite: 398 passed, 1 skipped (baseline 385 + 13 new)

## What shipped
- `ragspine/core/spine.py`: `clients.pausal_eur REAL DEFAULT 0` (negotiated
  monthly flat fee) via `_ensure_columns`; `cjenik` gets `key TEXT` +
  `unit TEXT` via the same helper, plus a `CREATE UNIQUE INDEX IF NOT EXISTS
  idx_cjenik_key ON cjenik(key)` so `INSERT OR IGNORE` seeding is idempotent.
- `ragspine/business/cjenik.py` (new):
  - `DEFAULT_CJENIK` — 9 Croatian accounting-service line items
    (mjesečno knjigovodstvo, obračun plaće/zaposleniku, PDV prijava, JOPPD,
    godišnji izvještaji, porezno savjetovanje/h, osnivanje tvrtke,
    zatvaranje/likvidacija, izrada fin. izvještaja), each `{key, usluga,
    cijena, unit}`.
  - `seed(spine)` — `INSERT OR IGNORE`, returns rowcount; idempotent (0 on
    2nd call). Wired into `ops/seeds.all()` as `"cjenik"` alongside the
    existing quickref/kalendar/dnevnice seeds.
  - `price_list`, `get_price(spine, key, default)` (matches `key` OR
    `usluga`, so legacy rows without a key still resolve).
  - `izracunaj_cijenu(spine, client_id, employees=0, extras=None)` — base =
    `client.pausal_eur` or the default mjesečno-knjigovodstvo price if 0;
    `+ obracun_place × employees` and `+ joppd_obrazac` when `employees>0`;
    `+ pdv_prijava` when `pdv_status` contains "u sustavu" (case-insensitive);
    `+ extras` (each an arbitrary cjenik key/usluga). All money via
    `Decimal.quantize(0.01, ROUND_HALF_UP)`; `ukupno` == sum of itemized
    `stavke` by construction (same `_d()` helper on both sides). Unknown
    `client_id` raises `ValueError`.
  - `usporedi_s_trzistem(spine, client_id)` — average `pausal_eur` across
    other *active*, `pausal_eur>0` clients; ±15% band → "ispod
    tržišta"/"iznad tržišta"/"u skladu"; no comparable peers → neutral
    Croatian message, `prosjek_trzista: None`.
- `ragspine/web/api.py` (`require_user_web`, matches existing endpoint
  style): `GET /cjenik`, `POST /cjenik/izracun`, `GET
  /cjenik/usporedba/{client_id}`, `POST /clients/{id}/pausal`. `ValueError`
  (unknown client) → HTTP 404.
- `tests/test_cjenik.py` (13 tests, TDD): seed count + idempotency, get_price
  known/default, price_list count, PDV+employee quote (line items sum to
  total, PDV/JOPPD lines present), non-PDV client omits PDV line, zero-pausal
  client falls back to default base price, market comparison below/at/absent,
  and 4 API round-trip tests (list, izracun, usporedba, set-pausal-then-used).

## Concerns
- **Prices are illustrative, operator-adjustable** (ponytail): `DEFAULT_CJENIK`
  EUR amounts are plausible defaults for a small HR knjigovodstveni ured, not
  a market survey. No override layer was added — unlike `quickref` (legal
  figures needing drift-tracking against NN/Porezna), this is the firm's own
  commercial price, so a direct `UPDATE cjenik SET cijena=... WHERE key=...`
  (or a future admin UI) is the intended adjustment path.
- `usporedi_s_trzistem`'s ±15% band is a fixed heuristic constant, not
  configurable — matches the task spec ("below avg by >15%" / "above by
  >15%") as literally stated; upgrade path is a `config_overrides` key if an
  operator wants to tune it later.
- `extras` items resolve by key-or-usluga against the live `cjenik` table;
  an unknown extra key silently contributes a 0.00 line with its raw key as
  the label rather than erroring — chosen for symmetry with `get_price`'s
  default-on-miss behavior, not tested explicitly beyond the itemized-sum
  invariant.
