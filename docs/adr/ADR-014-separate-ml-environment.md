# ADR-014: Separate Python environment for ML training

- Status: Accepted
- Date: 2026-09-30

## Context
Fine-tuning the parser model and training the risk model need PyTorch, Transformers, PEFT/TRL
and LightGBM. Together these are several GB and pin their own CUDA and NumPy versions. The
backend services only need lightweight inference (onnxruntime) and must stay small enough for
free-tier ARM VMs.

## Decision
`ml/` is its own uv project (`kharcha-ml`) with its own `pyproject.toml` and `uv.lock`, and it
is not part of the backend workspace. It produces artifacts (GGUF, ONNX, eval reports) that
the backend consumes through `model_versions.artifact_uri` + sha256. Kaggle/Colab notebooks are
thin wrappers that install and call the `kharcha_ml` package.

## Alternatives considered
- **One environment for everything**: simpler imports, but every service image would carry
  training dependencies and dependency conflicts would block backend upgrades.
- **ML as an optional extra of the backend workspace**: it still shares one lockfile, so
  resolver conflicts remain.

## Consequences
- Backend images stay lean and backend dependency upgrades are not blocked by ML libraries.
- Code shared between the two environments (for example the extraction output schema) must be
  kept in sync explicitly, through exported JSON Schemas or a small copied module checked by
  a test.
- CI runs two independent jobs (`backend`, `ml`).
