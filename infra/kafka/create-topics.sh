#!/usr/bin/env bash
# Create Kafka topics (PROJECT_SPEC §7.2) and their .DLT topics. Idempotent.
# Must match backend/packages/common/src/kharcha_common/topics.py (contract test checks it).
set -euo pipefail

BOOTSTRAP="${KAFKA_BOOTSTRAP:-kafka:19092}"
KAFKA_TOPICS="${KAFKA_TOPICS_BIN:-/opt/kafka/bin/kafka-topics.sh}"
DLT_RETENTION_DAYS=30
DAY_MS=86400000

create() {
  local name="$1" partitions="$2" days="$3"
  "$KAFKA_TOPICS" --bootstrap-server "$BOOTSTRAP" --create --if-not-exists \
    --topic "$name" --partitions "$partitions" --replication-factor 1 \
    --config "retention.ms=$((days * DAY_MS))" --config min.insync.replicas=1
  echo "ok: $name (partitions=$partitions retention=${days}d)"
}

# name  local-partitions  retention-days
while read -r name partitions days; do
  [ -z "$name" ] && continue
  create "$name" "$partitions" "$days"
  create "$name.DLT" "$partitions" "$DLT_RETENTION_DAYS"
done <<'TOPICS'
raw-events            3  30
parsed-transactions   3  7
clean-transactions    3  7
cash-events           3  7
agent-tasks           3  3
agent-results         3  7
user-feedback         3  30
nudge-outcomes        3  30
model-shadow          3  7
TOPICS

echo "all topics ready"
