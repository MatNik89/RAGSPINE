# T12 — model recommender + LiteLLM config

## What was built
- `ragspine/ops/model_recommender.py`: `classify_tier`, `recommend`,
  `litellm_config`, `pull_commands`, `report`. 5 tiers (tiny/small/medium/
  large/dgx) by total RAM+VRAM, concrete Ollama tags per role
  (chat/embed/utility/vlm), unavailable roles get a HR warning instead of a
  model. Reuses `setup.detect_hw()` (no hardware detection duplicated); GPU
  VRAM parsed from the `gpu` string via regex, 0 if unparseable.
- CLI: `ragspine models` -> `report()`.
- API: `GET /models/recommend` (JSON) and `GET /models/litellm` (text/plain
  YAML), both behind `require_user_web` as instructed.
- `setup.run()` appends a best-effort one-line tier+chat recommendation,
  wrapped in try/except so it never breaks the existing setup report.

## Tests (TDD, tests/test_model_recommender.py)
Written first (confirmed failing on missing module), then implemented to
green: tier bucketing + boundaries, recommend() shape/warn-for-tiny,
GPU VRAM parsing (parseable + unparseable), litellm_config content,
pull_commands dedup, report content, and 2 API tests through TestClient.
All hardware-dependent tests inject `hw=` — no real subprocess/nvidia-smi
touched.

## Full suite
444 baseline + 12 new = 456 passed, 1 skipped (pre-existing skip), 0 failed.

## Notes / ponytail
- Model tags are recommendations only — no auto-pull, no telemetry;
  operator runs the printed `ollama pull` commands themselves.
- `_already_pulled` best-effort hits `http://127.0.0.1:11434/api/tags` with a
  1s timeout, swallowed on any error (offline-safe, doesn't slow tests).
