from __future__ import annotations

import argparse
import os
from pathlib import Path

from ad_factory import (
    build_week1_variants,
    maybe_generate_images_automatic1111,
    refine_copy_with_ollama,
    write_outputs,
)


def main() -> int:
    p = argparse.ArgumentParser(description="Generate Week 1 SA ad variants (copy + image prompts).")
    p.add_argument("--out", default="output/marketing", help="Output folder")
    p.add_argument("--refine", action="store_true", help="Refine copy using local Ollama (OpenAI-compatible endpoint)")
    p.add_argument("--ollama-base-url", default=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"))
    p.add_argument(
        "--ollama-model",
        default=(
            os.getenv("LLM_MODEL_MARKETING")
            or os.getenv("OPENAI_MODEL_MARKETING")
            or os.getenv("OLLAMA_MODEL_MARKETING")
            or os.getenv("OLLAMA_MODEL")
            or "llama3.1:8b"
        ),
    )
    p.add_argument("--images", action="store_true", help="Generate images via AUTOMATIC1111 WebUI API")
    p.add_argument("--a1111-url", default=os.getenv("A1111_URL", "http://127.0.0.1:7860"))
    args = p.parse_args()

    variants = build_week1_variants()
    if args.refine:
        variants = refine_copy_with_ollama(variants, base_url=args.ollama_base_url, model=args.ollama_model)

    out_dir = Path(args.out)
    json_path, csv_path = write_outputs(variants, out_dir=out_dir, stem="week1_ads")

    if args.images:
        maybe_generate_images_automatic1111(variants, out_dir=out_dir / "images", a1111_url=args.a1111_url)

    print(f"Wrote: {json_path}")
    print(f"Wrote: {csv_path}")
    if args.images:
        print(f"Images (if generated): {out_dir / 'images'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
