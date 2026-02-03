from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


def default_env_file_path() -> Path:
    """Return the default path to the runtime env file.

    Priority:
    - `EVIDEX_ENV_PATH` if set
    - repo root `evidex.env`

    This file is intentionally *not* a dotenv dependency; we keep a tiny parser
    here so the engine can run with minimal installs.
    """

    override = (os.getenv("EVIDEX_ENV_PATH") or "").strip()
    if override:
        return Path(override).expanduser().resolve()

    try:
        repo_root = Path(__file__).resolve().parents[2]
    except Exception:
        repo_root = Path.cwd()

    return (repo_root / "evidex.env").resolve()


def _iter_env_lines(text: str) -> Iterable[tuple[str, str]]:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        # allow `export KEY=...`
        if line.lower().startswith("export "):
            line = line[7:].lstrip()

        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        # Strip surrounding quotes.
        if len(value) >= 2 and ((value[0] == value[-1] == '"') or (value[0] == value[-1] == "'")):
            value = value[1:-1]

        yield key, value


def load_env_file(path: Path, *, override: bool = False) -> None:
    """Load env vars from `path` into `os.environ`.

    If `override` is False, existing process env vars win.
    """

    if not path.exists() or not path.is_file():
        return

    text = path.read_text(encoding="utf-8", errors="replace")
    for key, value in _iter_env_lines(text):
        if override or key not in os.environ:
            os.environ[key] = value


def load_default_env(*, override: bool = False) -> Path:
    """Load the default env file (if present) and return its path."""

    path = default_env_file_path()
    load_env_file(path, override=override)
    return path


def _format_env_value(value: str) -> str:
    # Keep it simple: quote only when needed.
    v = "" if value is None else str(value)
    if v == "":
        return ""
    needs_quotes = any(ch.isspace() for ch in v) or "#" in v
    if needs_quotes:
        v = v.replace('"', "\\\"")
        return f'"{v}"'
    return v


def save_env_file(path: Path, updates: dict[str, str]) -> None:
    """Update/create an env file, preserving unrelated lines.

    - Updates keys in-place when present
    - Appends missing keys at the end
    """

    path.parent.mkdir(parents=True, exist_ok=True)

    existing_lines: list[str] = []
    if path.exists():
        existing_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    key_to_index: dict[str, int] = {}
    for i, raw in enumerate(existing_lines):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key and key not in key_to_index:
            key_to_index[key] = i

    lines = list(existing_lines)
    for key, value in updates.items():
        rendered = f"{key}={_format_env_value(value)}"
        if key in key_to_index:
            lines[key_to_index[key]] = rendered
        else:
            lines.append(rendered)

    # Ensure trailing newline.
    out = "\n".join(lines).rstrip("\n") + "\n"
    path.write_text(out, encoding="utf-8")
