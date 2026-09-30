"""Tier 4 teacher LLM via LiteLLM (PROJECT_SPEC §10.1, §10.3)."""

from typing import Protocol

import litellm

from kharcha_common.prompts import Prompt, load_prompt
from kharcha_common.settings import Settings
from kharcha_processor.extraction import ExtractionResult, build_messages, decode_extraction


class BadModelOutputError(ValueError):
    """The model's output could not be decoded into an :class:`ExtractionResult`."""


class Extractor(Protocol):
    """Anything that turns one message into an :class:`ExtractionResult`."""

    model_version: str

    async def extract(
        self, *, sender: str | None, source_app: str | None, text: str
    ) -> ExtractionResult: ...


class TeacherLLM:
    def __init__(self, settings: Settings, prompt: Prompt | None = None) -> None:
        self._settings = settings
        self._prompt = prompt or load_prompt("parser")
        self.model_version = f"{settings.llm_model}#{self._prompt.ref}"

    async def extract(
        self, *, sender: str | None, source_app: str | None, text: str
    ) -> ExtractionResult:
        is_ollama = self._settings.llm_model.startswith("ollama")
        response = await litellm.acompletion(
            model=self._settings.llm_model,
            messages=build_messages(self._prompt, sender=sender, source_app=source_app, text=text),
            api_base=self._settings.ollama_api_base if is_ollama else None,
            temperature=0,
            response_format={"type": "json_object"},
            timeout=self._settings.llm_timeout_s,
        )
        content = response.choices[0].message.content or ""
        try:
            return decode_extraction(content)
        except ValueError as exc:  # includes JSONDecodeError and pydantic ValidationError
            raise BadModelOutputError(type(exc).__name__) from exc
