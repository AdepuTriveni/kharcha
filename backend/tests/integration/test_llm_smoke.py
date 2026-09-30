"""LiteLLM smoke test against the configured model (default: local Ollama).

Skipped when Ollama is not reachable or the model is not pulled.
Run: ``uv run pytest -m integration tests/integration/test_llm_smoke.py``
"""

import httpx
import litellm
import pytest

from kharcha_common.settings import Settings

pytestmark = pytest.mark.integration


def _ollama_has_model(settings: Settings) -> bool:
    name = settings.llm_model.removeprefix("ollama/")
    try:
        response = httpx.get(f"{settings.ollama_api_base}/api/tags", timeout=3)
        response.raise_for_status()
    except httpx.HTTPError:
        return False
    names = {m["name"] for m in response.json().get("models", [])}
    return name in names or f"{name}:latest" in names


async def test_llm_replies() -> None:
    settings = Settings()
    is_ollama = settings.llm_model.startswith("ollama/")
    if is_ollama and not _ollama_has_model(settings):
        pytest.skip(f"Ollama not reachable or {settings.llm_model} not pulled")

    response = await litellm.acompletion(
        model=settings.llm_model,
        messages=[{"role": "user", "content": "Reply with the single word: pong"}],
        api_base=settings.ollama_api_base if is_ollama else None,
        max_tokens=10,
        temperature=0,
        timeout=120,
    )
    text = response.choices[0].message.content or ""
    assert "pong" in text.lower()
