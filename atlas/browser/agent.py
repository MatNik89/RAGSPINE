"""CDP browser agent via optional browser-use library. Degrades cleanly when absent."""
from atlas.core import optional
from atlas.core.llm import LLMClient


def available() -> bool:
    return optional.need("browser_use", "CDP browser agent") is not None


def run_task(cfg, task: str, url: str = "") -> dict:
    if not available():
        return {"error": "browser-use nije instaliran", "hint": "pip install browser-use"}

    # ponytail: v1 = thin wrapper only, untested (browser-use not installed in
    # this env). Real integration is a straight pass-through: wrap LLMClient in
    # a minimal langchain-shaped adapter (.ainvoke) and hand it to browser_use.Agent.
    # Upgrade path once browser-use is actually installed: run this against a
    # real CDP session, fix whatever the real Agent()/run() signature expects.
    try:
        import browser_use

        class _LLMAdapter:
            def __init__(self, client: LLMClient):
                self._client = client

            async def ainvoke(self, messages, **kwargs):
                result = self._client.complete(messages)
                return result.text

        bagent = browser_use.Agent(task=f"{task} {url}".strip(), llm=_LLMAdapter(LLMClient(cfg)))
        result = bagent.run()
        return {"result": result}
    except Exception as e:
        return {"error": str(e)}
