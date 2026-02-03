from __future__ import annotations

import os
from dataclasses import dataclass


def _env_truthy(name: str, default: str = "") -> bool:
    v = os.getenv(name, default)
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class LlmConfig:
    api_key: str | None
    model: str
    base_url: str | None


def load_llm_config() -> LlmConfig:
    # Support OpenAI-compatible endpoints (including local Ollama).
    #
    # Preferred envs for hosted OpenAI:
    #   OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL
    #
    # Convenience aliases for local Ollama:
    #   OLLAMA_BASE_URL (typically http://localhost:11434/v1)
    #   OLLAMA_MODEL (e.g. llama3.2:latest)
    #   OLLAMA_API_KEY (optional; typically not needed, defaults to 'ollama')

    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OLLAMA_BASE_URL")
    model = os.getenv("OPENAI_MODEL") or os.getenv("OLLAMA_MODEL") or "gpt-4o-mini"
    api_key = os.getenv("OPENAI_API_KEY")

    if (not api_key) and base_url:
        # Only auto-enable a dummy key for local endpoints to avoid accidentally
        # sending data to a remote endpoint without credentials.
        b = str(base_url).lower()
        if any(h in b for h in ["localhost", "127.0.0.1", "0.0.0.0"]):
            api_key = os.getenv("OLLAMA_API_KEY") or "ollama"

    return LlmConfig(api_key=api_key, model=model, base_url=base_url)


class LlmClient:
    def __init__(self, cfg: LlmConfig):
        self._cfg = cfg
        self._client = None
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        # Allow a global kill-switch so operators can disable LLM usage from the
        # desktop UI without removing endpoint configuration.
        if _env_truthy("LLM_DISABLED", "0"):
            return False
        return bool(self._cfg.api_key)

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self._cfg.api_key:
            return None
        from openai import OpenAI  # lazy import

        # Critical: set explicit timeouts so a missing local Ollama (or a stalled network)
        # doesn't hang pack generation.
        try:
            timeout_s = float(os.getenv("LLM_TIMEOUT_SECONDS", "12"))
        except Exception:
            timeout_s = 12.0
        try:
            max_retries = int(os.getenv("LLM_MAX_RETRIES", "0"))
        except Exception:
            max_retries = 0

        self._client = OpenAI(
            api_key=self._cfg.api_key,
            base_url=self._cfg.base_url,
            timeout=timeout_s,
            max_retries=max_retries,
        )
        return self._client

    def complete(self, *, system: str, user: str, model: str | None = None) -> str:
        if not self.enabled:
            return ""
        client = self._get_client()
        if client is None:
            return ""
        try:
            self.last_error = None
            resp = client.chat.completions.create(
                model=(model or self._cfg.model),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            # Fail-soft: misconfigured endpoint, model missing, or transient network issues.
            try:
                self.last_error = f"{type(e).__name__}: {e}"
            except Exception:
                self.last_error = type(e).__name__
            return ""
