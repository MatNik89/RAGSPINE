"""Chat orchestrator: wires router -> cache -> kb -> lane handlers -> retrieval/LLM/citations."""
import json

from ragspine.business import monthly
from ragspine.core.llm import LLMError, LLMUnavailable
from ragspine.knowledge import features, kb
from ragspine.rag import (
    authority, cache, citations, clarify, client_context, composer, conversation,
    retrieval, router, selfrag,
)

# Later tasks register sql/learn/web/graph/ocr handlers here.
# Signature: handler(spine, cfg, query, llm) -> str|None; None falls back to chat lane.
LANE_HANDLERS: dict[str, callable] = {}

_REJECT_MSG = "Ne mogu izvršiti taj zahtjev."
_GREETING = "Bok! Kako vam mogu pomoći?"
_LLM_DOWN = "LLM trenutno nedostupan ili je vratio grešku."


def _package(answer_text: str, lane: str, confidence: float, sources: list, cached: bool) -> dict:
    return {"answer": answer_text, "lane": lane, "confidence": confidence,
            "sources": sources, "cached": cached}


def _record(spine, user: str, query: str, lane: str, answer_text: str, confidence: float,
            cache_write: bool = True) -> None:
    if cache_write:
        cache.put(spine, query, answer_text, meta=json.dumps({"lane": lane, "confidence": confidence}))
    with spine.write() as c:
        c.execute(
            "INSERT INTO interactions(user,query,lane,answer,confidence) VALUES(?,?,?,?,?)",
            (user, query, lane, answer_text, confidence),
        )


def answer(spine, cfg, query: str, user: str, llm=None, fresh: bool = False) -> dict:
    lane = router.route(query)

    if lane == "reject":
        return _package(_REJECT_MSG, "reject", 0, [], False)

    if monthly.MONTHLY_RE.search(query):
        period = monthly._period_now()
        text = monthly.format_overview(monthly.overview(spine, period))
        return _package(text, "monthly", 1.0, [], False)

    if lane == "no_retrieval":
        text = _GREETING
        if llm is not None:
            try:
                text = llm.complete([{"role": "user", "content": query}]).text
            except (LLMUnavailable, LLMError):
                pass
        return _package(text, "no_retrieval", 1.0, [], False)

    # W2 clarify gate: an under-specified how-to ("kako se radi plaća") with
    # ≥2 approved SOP variants (different client/type) gets asked back
    # instead of guessed. Only on the plain chat path; best-effort so a
    # clarify bug never blocks a normal answer.
    if lane == "chat":
        try:
            clarification = clarify.needs_clarification(spine, query)
        except Exception:
            clarification = None
        if clarification is not None:
            return {"answer": clarification["question"], "lane": "clarify", "confidence": 1.0,
                    "sources": [], "cached": False, "clarify": True,
                    "variants": clarification["variants"]}

    # W3: resolve early whether the query names a specific client. A
    # client-named query is client-specific, so it must (a) skip the generic
    # text-keyed cache — same reasoning as the has_history skip below — and
    # (b) get a client-scoped napomena appended later regardless of whether
    # citation verification succeeds. Best-effort: never break the answer.
    try:
        resolved_client = client_context.resolve_client(spine, query)
    except Exception:
        resolved_client = None

    # Fetch history early: a user with prior turns is mid-conversation, so a
    # text-keyed cache hit/write for their query would silently splice in (or
    # leak into) an unrelated conversation's context. Only cache first turns.
    prior_turns = []
    if not fresh:
        try:
            prior_turns = conversation.recent_turns(spine, user)
        except Exception:
            prior_turns = []  # ponytail: memory is best-effort — never break the answer
    has_history = bool(prior_turns)
    skip_cache = has_history or resolved_client is not None

    if not skip_cache:
        cached_answer = cache.get(spine, query)
        if cached_answer is not None:
            return _package(cached_answer, "chat", 1.0, [], True)

    # W3: a client-named query also skips the kb fast-path, for the same
    # reason as the cache skip above — a kb hit keyed on plain query text may
    # have been saved for a different (or no) client and would silently drop
    # the napomena/"client" key on repeat.
    kb_answer = kb.lookup(spine, query) if resolved_client is None else None
    if kb_answer is not None:
        return _package(kb_answer, "chat", 0.9, [], False)

    handler = LANE_HANDLERS.get(lane)
    if handler is not None:
        res = handler(spine, cfg, query, llm)
        if res is not None:
            _record(spine, user, query, lane, res, 1.0, cache_write=not skip_cache)
            return _package(res, lane, 1.0, [], False)

    # chat lane (or unhandled lane falling through)
    hits = retrieval.search(spine, query, k=selfrag.k_for(query))

    if not selfrag.check_relevance(llm, query, hits):
        web_handler = LANE_HANDLERS.get("web")
        if web_handler is not None:
            res = web_handler(spine, cfg, query, llm)
            if res is not None:
                _record(spine, user, query, "web", res, 1.0, cache_write=not skip_cache)
                return _package(res, "web", 1.0, [], False)

    system, messages = composer.compose(query, hits)
    messages = conversation.as_messages(prior_turns) + messages

    if llm is None:
        return _package(_LLM_DOWN, "chat", 0, [], False)
    try:
        result = llm.complete(messages, system=system)
    except (LLMUnavailable, LLMError):
        return _package(_LLM_DOWN, "chat", 0, [], False)

    report = citations.verify(result.text, hits)

    # W3: the client-specific napomena is independent of citation verification
    # (it comes from clients/sop_pages/notes, not from `hits`) — compute it
    # unconditionally so it can surface even when the generic answer is IDK,
    # which is exactly when the client's own SOP/note is most needed.
    # Best-effort, must never break the answer.
    napomena_block = ""
    if resolved_client is not None:
        try:
            napomena_block = client_context.client_note_block(
                spine, resolved_client["id"], resolved_client["name"], query)
        except Exception:
            napomena_block = ""

    if not report.ok:
        if resolved_client is not None and napomena_block:
            final_text = f"Ne znam općenito, ali imam napomenu za ovog klijenta:\n\n{napomena_block}"
        else:
            final_text = citations.IDK
        confidence, sources = 0, []
    else:
        final_text = result.text
        cited_hits = [hits[n - 1] for n in report.cited if 1 <= n <= len(hits)]
        confidence = citations.blend_authority(report.confidence, cited_hits)
        sources = [{"n": n, "title": hits[n - 1].title, "doc_id": hits[n - 1].doc_id}
                   for n in report.cited]
        try:
            related = authority.related_documents(spine, hits)
            if related:
                titles = ", ".join(r["title"] for r in related)
                final_text = f"{final_text}\n\n📎 Povezani dokumenti: {titles}"
        except Exception:
            pass
        if napomena_block:
            final_text = f"{final_text}\n\n{napomena_block}"

    _record(spine, user, query, "chat", final_text, confidence, cache_write=not skip_cache)
    try:
        features.maybe_file_gap(spine, user, query, final_text, confidence)
    except Exception:
        pass  # ponytail: capability-gap filing is best-effort, must never break the chat lane
    if report.ok and resolved_client is None:
        kb.save(spine, query, final_text)
    result = _package(final_text, "chat", confidence, sources, False)
    if resolved_client is not None:
        result["client"] = {"id": resolved_client["id"], "name": resolved_client["name"]}
    return result
