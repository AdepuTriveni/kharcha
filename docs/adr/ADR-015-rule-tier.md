# ADR-015: Tier 1 parse rules: global rules, per-user synthesis, shadow-earned trust

- Status: Accepted
- Date: 2026-10-01

## Context
§10.4 asks for regex rules that take most messages off the LLM path. The teacher writes the
rules, and an LLM-written regex can be wrong, slow (catastrophic backtracking) or leak data.
`parse_rules` has no `user_id`, so one rule serves every user who gets that bank template,
while rule 7 says every data access is scoped by `user_id`.

## Decision
- Rules are global, keyed by the **sender key** (`AX-HDFCBK` -> `HDFCBK`, or the app package).
- Synthesis samples come only from the **same user's** teacher-parsed messages of the same
  template signature (current message + up to 4 earlier ones; at least 2 in total).
- A compile guard rejects nested quantifiers, backreferences, regexes over 600 characters,
  unknown groups, and **literal data** (3+ digit runs or `x@y` VPAs outside escapes), so a rule
  learned from one user cannot carry that user's data to others. Texts over 1000 characters
  are never matched.
- A rule must reproduce every sample exactly before it is stored as CANDIDATE. Candidates only
  run in shadow, next to the teacher. ACTIVE needs 5 matches with 0 mismatches; any rule is
  DISABLED once `mismatch_count > 2`. Hand-written seed rules start ACTIVE.
- 10% of rule-parsed events (chosen by a hash of the event id, so redelivery repeats the
  choice) also run the teacher. The comparison goes to `model-shadow`, and mismatches count
  toward auto-disable. The rule's output stays authoritative for that event.
- Counter updates happen in the same transaction as `processed_events`, so redelivery never
  double-counts. Synthesis runs after that commit as a best-effort step, with a per-process,
  per-template cooldown of 6 hours. Rule ids are a hash of (sender key, regex), so concurrent
  workers insert the same rule at most once.

## Alternatives considered
- **Per-user rules**: avoids sharing but multiplies LLM calls by the number of users and
  learns slower. Templates belong to banks, not users.
- **Cross-user sampling**: faster learning, but breaks rule 7.
- **The `regex` package with timeouts**: an extra dependency. The structural guard plus the
  length cap plus shadow trust is enough for now; revisit if a rule ever hangs.

## Consequences
- New templates need two messages from one user before a rule can appear.
- The cooldown is per process. Several processor replicas may each try once per template;
  the deterministic ids keep the table clean.
- The phone's cached rule set (§10.1) can later download only ACTIVE rules.
