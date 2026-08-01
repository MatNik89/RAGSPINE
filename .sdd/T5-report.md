# T5 — Multi-turn conversation memory

Status: DONE
Commit: 6bb9f48
Full suite: 382 passed, 1 skipped (baseline 375 + 7 new tests)

## What shipped
- `ragspine/rag/conversation.py`: `recent_turns(spine, user, limit=5)` (per-user,
  chronological, ORDER BY id to avoid same-second `at` ties) and
  `as_messages(turns)` (alternating user/assistant, answers capped to 500 chars).
- `ragspine/rag/pipeline.py` chat lane only: prior turns fetched and prepended to
  `messages` right before `llm.complete`, wrapped in try/except (memory failure
  never breaks the answer). Added `fresh: bool = False` param to `answer()` to
  skip history. sql/reject/no_retrieval/monthly short-circuits untouched.
- `ragspine/web/api.py`: `ChatBody.fresh` flag, threaded through `/chat`.
- `tests/test_conversation.py`: recent_turns ordering/isolation/limit,
  as_messages shape/truncation, pipeline multi-turn context injection
  (captured via fake LLM transport), per-user isolation, fresh-flag skip,
  and memory-failure-doesn't-break-answer (monkeypatched `recent_turns` raising).

## Concerns
- `query_cache` (used for the second-call-cache shortcut) is keyed only by
  normalized query text, not by user — pre-existing behavior, unrelated to
  this feature, not touched. **Addressed in the fix round below.**
- History is unbounded across lanes (any past interaction row, not just
  "chat"), per the task's "keep it simple" instruction — reject rows never
  reach `interactions` so this is moot in practice.

## Fix round — review found 1 Critical + 1 Minor

Status: DONE
Commit: (see below)
Full suite: 385 passed, 1 skipped (382 baseline + 3 new tests)

### 1. CRITICAL — stale cross-context cache hit once a user has history
`query_cache` is keyed by query text only and was checked *before* history
was spliced in. A short elliptical follow-up ("a za Zadar?") that repeats
verbatim across unrelated conversations could return another context's
cached answer, silently skipping history + citations.

Fix (`ragspine/rag/pipeline.py`): `conversation.recent_turns(spine, user)`
is now fetched once, early (right after the reject/monthly/no_retrieval
short-circuits, before the cache read). `has_history = bool(prior_turns)`
gates both the cache **read** (`cache.get` only tried when
`not has_history`) and the cache **write** (`_record` gained a
`cache_write: bool = True` kwarg, called with `cache_write=not has_history`
from all three call sites: handler-lane, web-fallback, and chat-lane). Net
effect: `query_cache` now only serves/populates a user's genuinely
context-free first turn; from the second turn on, every query — even a
verbatim repeat — goes through the normal history + retrieval + citations
path. `fresh=True` still means "no history, so cache behaves exactly as
before" (used by `/v1/chat/completions`, see below).

`prior_turns` is now reused for the actual message-splice (no second DB
round trip): `messages = conversation.as_messages(prior_turns) + messages`.

Test added: `tests/test_conversation.py::test_pipeline_cache_bypassed_once_user_has_history`
— seeds a stale `query_cache` entry for text "a za Zadar?" plus a prior
interaction for user "ana" (giving her history), then asserts a fresh
`answer()` call with that same text does NOT return the stale cached text,
`cached` is `False`, and the LLM was actually invoked with the prior turn
present in its messages.

Side effect requiring a pre-existing test update:
`tests/test_pipeline.py::test_cache_second_call` asserted that an
*immediate stateful repeat* of the same query (2nd call, same user, no
`fresh`) is served from cache — that is now precisely the unsafe case the
fix closes, since by the 2nd call the user already has history. Updated
the 2nd call to pass `fresh=True`, which is the legitimate case
`query_cache` remains an optimization for (documented inline with a
pointer to the new test).

### 2. MINOR — /v1/chat/completions was silently stateful
OpenAI-compat clients manage their own message history; the server should
not additionally splice in server-side conversation memory for that
endpoint.

Fix (`ragspine/web/api.py`): `/v1/chat/completions` now calls
`_answer(query, user, fresh=True)`. `/chat` is unchanged (`fresh=body.fresh`,
default `False` → stateful). `answer()`'s `fresh` param already existed
from the base T5 change, so no pipeline signature change was needed here.

Tests added (`tests/test_chat_api.py`): `test_chat_completions_is_stateless_fresh`
and `test_chat_is_stateful_by_default` — both monkeypatch `pipeline.answer`
with a spy wrapper that records the `fresh` kwarg it was called with (while
still delegating to the real implementation), then hit `/v1/chat/completions`
and `/chat` respectively through the FastAPI `TestClient` and assert
`fresh` was `True` / `False`.
