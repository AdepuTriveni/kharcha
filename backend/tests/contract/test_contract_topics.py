"""infra/kafka/create-topics.sh must create exactly the topics in kharcha_common.topics."""

import re
from pathlib import Path

from kharcha_common.topics import DLT_RETENTION_DAYS, TOPIC_SPECS

SCRIPT = Path(__file__).resolve().parents[3] / "infra" / "kafka" / "create-topics.sh"
LINE = re.compile(r"^\s*([a-z-]+)\s+(\d+)\s+(\d+)\s*$")


def test_script_matches_topic_specs() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    block = text.split("<<'TOPICS'", 1)[1].split("\nTOPICS", 1)[0]
    in_script = {
        m.group(1): (int(m.group(2)), int(m.group(3)))
        for line in block.splitlines()
        if (m := LINE.match(line))
    }
    expected = {t.value: (s.partitions_local, s.retention_days) for t, s in TOPIC_SPECS.items()}
    assert in_script == expected
    assert f"DLT_RETENTION_DAYS={DLT_RETENTION_DAYS}" in text
