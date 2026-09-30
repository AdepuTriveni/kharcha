"""Prompt registry (PROJECT_SPEC §27.1): ``backend/prompts/<agent>/vN.md`` with front-matter.

Front-matter is a small ``key: value`` block between ``---`` lines
(``version``, ``model``, ``temperature``, ``changelog``).
"""

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[4] / "prompts"
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)
_VERSION_FILE = re.compile(r"^v(\d+)\.md$")


@dataclass(frozen=True)
class Prompt:
    agent: str
    version: str
    body: str
    meta: dict[str, str] = field(default_factory=dict)

    @property
    def ref(self) -> str:
        """Value stored in ``agent_runs.prompt_version``, e.g. ``parser/v1``."""
        return f"{self.agent}/{self.version}"

    def section(self, name: str) -> str:
        """Text under a ``## <name>`` heading."""
        match = re.search(rf"^## {re.escape(name)}\n(.*?)(?=^## |\Z)", self.body, re.S | re.M)
        if match is None:
            raise KeyError(f"prompt {self.ref} has no section {name!r}")
        return match.group(1).strip()


def parse_prompt(agent: str, text: str) -> Prompt:
    match = _FRONT_MATTER.match(text.replace("\r\n", "\n"))
    if match is None:
        raise ValueError(f"prompt for {agent} is missing front-matter")
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if line.strip():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    if "version" not in meta:
        raise ValueError(f"prompt for {agent} has no version")
    return Prompt(agent=agent, version=meta["version"], body=match.group(2), meta=meta)


@cache
def load_prompt(agent: str, version: str | None = None, root: Path = PROMPTS_DIR) -> Prompt:
    """Load ``<root>/<agent>/<version>.md``; latest ``vN`` when ``version`` is None."""
    folder = root / agent
    if version is None:
        versions = [
            int(m.group(1)) for p in folder.glob("v*.md") if (m := _VERSION_FILE.match(p.name))
        ]
        if not versions:
            raise FileNotFoundError(f"no prompts in {folder}")
        version = f"v{max(versions)}"
    return parse_prompt(agent, (folder / f"{version}.md").read_text(encoding="utf-8"))
