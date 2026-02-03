# Marketing Scripts (Week 1 — South Africa)

This folder generates persona-specific ad copy + professional SDXL-style image prompts.

## What you get
- `output/marketing/week1_ads.json`
- `output/marketing/week1_ads.csv`
- (optional) `output/marketing/images/*.png` if you enable local image generation

## Copy refinement (local Ollama)
Prereq: run Ollama locally and pull a model.

Example:
- `ollama pull llama3.1:8b`

Then run:
- `C:/Users/User/Desktop/evidence-pack-engine/.venv/Scripts/python.exe scripts/marketing/generate_week1_ads.py --refine`

By default it uses:
- `OLLAMA_BASE_URL=http://localhost:11434/v1`
- `OLLAMA_MODEL=llama3.1:8b`

Model map (optional): use a different model for marketing refinement without changing your pack-generation model:
- `OLLAMA_MODEL_MARKETING=qwen2.5`

Aliases also supported:
- `LLM_MODEL_MARKETING`
- `OPENAI_MODEL_MARKETING`

## Optional image generation (local SDXL via AUTOMATIC1111)
Prereq: run AUTOMATIC1111 WebUI with API enabled (`--api`).

Then:
- set `A1111_URL=http://127.0.0.1:7860`
- run: `... generate_week1_ads.py --images`

This calls:
- `POST /sdapi/v1/txt2img`

Tip: keep one consistent style across ads (palette, typography, no logos). That tends to outperform random styles in B2B.
