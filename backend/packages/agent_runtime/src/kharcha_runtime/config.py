"""Load ``backend/config/agents.yaml`` (PROJECT_SPEC §16.6)."""

from pathlib import Path
from typing import Any

import yaml

from kharcha_common.prompts import load_prompt
from kharcha_runtime.types import AgentConfig, Limits

CONFIG_FILE = Path(__file__).resolve().parents[4] / "config" / "agents.yaml"


def load_agent_config(
    agent: str, default_model: str, path: Path = CONFIG_FILE
) -> tuple[AgentConfig, list[str]]:
    """Agent config plus its model fallback chain ("default" -> ``default_model``)."""
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))[agent]
    prompt_agent, version = str(data["prompt"]).split("/")
    prompt = load_prompt(prompt_agent, version)
    models = [default_model if m == "default" else str(m) for m in data.get("models", ["default"])]
    config = AgentConfig(
        name=agent,
        system_prompt=prompt.section("system"),
        prompt_version=prompt.ref,
        model=models[0],
        allowed_tools=frozenset(str(t) for t in data["tools"]),
        limits=Limits(**data.get("limits", {})),
    )
    return config, models
