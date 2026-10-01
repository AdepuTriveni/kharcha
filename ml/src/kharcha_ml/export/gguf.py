"""Merge LoRA -> GGUF -> quantize, record sha256, write an Ollama Modelfile (PROJECT_SPEC §15.6).

    uv run kharcha-export --adapter runs/qwen05-lora/adapter --base Qwen/Qwen2.5-0.5B-Instruct \\
        --llama-cpp ~/llama.cpp --version v1 --out exports/
    ollama create kharcha-parser:v1 -f exports/v1/Modelfile

Needs a llama.cpp checkout (convert_hf_to_gguf.py + a built ``llama-quantize``). The manifest
(``manifest.json``) is what the server stores in ``model_versions`` and serves to phones at
``/v1/models/parser/latest``.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

QUANTS = ("Q4_K_M", "Q5_K_M")
MIN_RAM_MB = {"Q4_K_M": 3_000, "Q5_K_M": 4_000, "F16": 6_000}


@dataclass(frozen=True)
class Manifest:
    version: str
    base_model: str
    quant: str
    file: str
    size_bytes: int
    sha256: str
    min_ram_mb: int
    prompt_version: str = "parser/v1"

    def to_json(self) -> str:
        return json.dumps({_camel(k): v for k, v in asdict(self).items()}, indent=2) + "\n"


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(w.title() for w in rest)


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def modelfile(gguf_name: str, system_prompt: str) -> str:
    """Ollama Modelfile: temperature 0, the §10.3 system prompt baked in."""
    escaped = system_prompt.replace('"""', "'''")
    return (
        f"FROM ./{gguf_name}\n"
        "PARAMETER temperature 0\n"
        "PARAMETER num_ctx 1024\n"
        f'SYSTEM """{escaped}"""\n'
    )


def manifest_for(gguf: Path, version: str, base: str, quant: str) -> Manifest:
    return Manifest(
        version=version,
        base_model=base,
        quant=quant,
        file=gguf.name,
        size_bytes=gguf.stat().st_size,
        sha256=sha256_file(gguf),
        min_ram_mb=MIN_RAM_MB.get(quant, 4_000),
    )


def merge_adapter(adapter: Path, base: str, out: Path) -> Path:  # pragma: no cover - needs torch
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model = AutoModelForCausalLM.from_pretrained(base, torch_dtype=torch.float16)
    merged = PeftModel.from_pretrained(model, str(adapter)).merge_and_unload()
    out.mkdir(parents=True, exist_ok=True)
    merged.save_pretrained(str(out), safe_serialization=True)
    AutoTokenizer.from_pretrained(base).save_pretrained(str(out))
    return out


def export(  # pragma: no cover - needs llama.cpp
    adapter: Path, base: str, llama_cpp: Path, version: str, out: Path, quant: str = "Q4_K_M"
) -> Manifest:
    from kharcha_ml.distill.teacher import load_sections

    target = out / version
    merged = merge_adapter(adapter, base, target / "merged")
    f16 = target / f"kharcha-parser-{version}-f16.gguf"
    subprocess.run(
        [
            sys.executable,
            str(llama_cpp / "convert_hf_to_gguf.py"),
            str(merged),
            "--outfile",
            str(f16),
            "--outtype",
            "f16",
        ],
        check=True,
    )
    quantized = target / f"kharcha-parser-{version}-{quant.lower()}.gguf"
    subprocess.run(
        [str(llama_cpp / "build" / "bin" / "llama-quantize"), str(f16), str(quantized), quant],
        check=True,
    )
    (target / "Modelfile").write_text(
        modelfile(quantized.name, load_sections()["system"]), encoding="utf-8"
    )
    (target / "Modelfile.f16").write_text(
        modelfile(f16.name, load_sections()["system"]), encoding="utf-8"
    )
    manifest = manifest_for(quantized, version, base, quant)
    (target / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    parser = argparse.ArgumentParser(prog="kharcha-export")
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--llama-cpp", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--quant", choices=QUANTS, default="Q4_K_M")
    parser.add_argument("--out", type=Path, default=Path("exports"))
    args = parser.parse_args(argv)
    manifest = export(args.adapter, args.base, args.llama_cpp, args.version, args.out, args.quant)
    print(manifest.to_json())
    return 0


def run() -> None:  # pragma: no cover
    raise SystemExit(main())
