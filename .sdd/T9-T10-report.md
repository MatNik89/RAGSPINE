# T9 + T10 — degraded-mode reminders.json dump + Croatian NL date parser

## T9 — ragspine/ops/reminders_dump.py
- `dump(spine, cfg, now_fn=None) -> {"path","count"}`: collects reminders
  (done=0, due within 30 days), `kalendar.upcoming(30)`, `expiry.expiring(30)`
  into one JSON payload with `generated`/`note`/`reminders`/`rokovi`/`istek`.
  Written to `{cfg.nas_root or cfg.data_dir}/reminders.json`, atomically
  (`.tmp` + `os.replace`).
- Scheduler job `reminders_dump_job` registered in `ops/jobs.py
  register_defaults` at `interval_s=3600`.
- CLI: `ragspine reminders dump` runs it once and prints the path.

## T10 — ragspine/business/nldate.py
- `parse_date(text, now_fn=None) -> str|None`: "za N dana/dan", "sutra",
  "prekosutra", "danas", Croatian weekday names ("u petak" etc, next
  occurrence, rolls +7 if today already matches), "do DD.MM[.YYYY]" /
  "DD.MM.YYYY" (year omitted -> current year, rolled to next year if already
  past). Diacritic-insensitive via `unicodedata.normalize("NFKD", ...)` +
  drop combining marks (covers č/ć/š/ž). Returns `None` on no match.
- `set_reminder_nl(spine, user, body, when_text, now_fn=None) -> {"id","due"}
  | {"error":"Ne razumijem datum"}`.
- CLI: `ragspine reminders add "<body>" "<when>"` tries `parse_date` first,
  falls back to accepting a literal `YYYY-MM-DD` (kept the existing
  ISO-date CLI test green).
- `POST /reminders {body, when}` (require_user_web) in `web/api.py` ->
  `set_reminder_nl`, 400 on unparseable date.

## Tests (TDD, written first and confirmed failing pre-implementation)
- `tests/test_nldate.py` — relative days, weekday roll, dot-dates incl.
  year-rollover, diacritic-insensitivity, garbage -> None,
  `set_reminder_nl` insert + error path (no insert on error).
- `tests/test_reminders_dump.py` — full dump + JSON shape, done/far-future
  reminders excluded, atomic overwrite on 2nd run, nas_root vs data_dir
  fallback, job registered with `interval_s=3600`.
- `tests/test_jobs.py` — bumped `test_register_defaults_registers_all_jobs`
  to include `"reminders_dump"`.
- Existing `tests/test_eval.py::test_cli_reminders_add_then_list` (ISO date
  path) still green.

## Result
Full suite: 435 passed, 1 skipped (baseline 423 + 12 new tests).

## Concerns / notes
- `parse_date`'s dot-date regex intentionally does not accept `YYYY-MM-DD`
  (dashes) — that's why the CLI has its own ISO fallback rather than routing
  through `set_reminder_nl` for that case; the web endpoint only accepts NL
  or dot-dates via `set_reminder_nl` per spec (no ISO fallback there).
- `reminders_dump` overwrites a single shared file — concurrent dumps from
  daemon + manual CLI could race on the same NAS path; `os.replace` keeps
  each individual write atomic so readers never see a half-written file,
  just possibly a slightly stale one. Acceptable for a degraded-mode readout.
