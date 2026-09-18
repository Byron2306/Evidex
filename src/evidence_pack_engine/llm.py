from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse


def _env_truthy(name: str, default: str = "") -> bool:
    v = os.getenv(name, default)
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class LlmConfig:
    api_key: str | None
    model: str
    base_url: str | None


def load_llm_config() -> LlmConfig:
    """Load the Phase 9 sovereign Evidex LLM configuration.

    Evidex may still use an OpenAI-compatible *client protocol*, but the endpoint
    is Ollama only. OPENAI_* credentials are deliberately ignored so an old cloud
    key cannot silently reactivate hosted inference.
    """

    base_url = (
        os.getenv("EVIDEX_OLLAMA_OPENAI_BASE_URL")
        or os.getenv("OLLAMA_OPENAI_BASE_URL")
        or "http://127.0.0.1:11434/v1"
    ).strip().rstrip("/")
    model = (
        os.getenv("EVIDEX_OLLAMA_MODEL")
        or os.getenv("OLLAMA_MODEL")
        or "qwen2.5:3b"
    ).strip()

    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Evidex Ollama endpoint must be a valid HTTP(S) URL.")

    if (
        parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        and not _env_truthy("EVIDEX_ALLOW_REMOTE_OLLAMA", "0")
    ):
        raise ValueError(
            "Evidex sovereign runtime refuses non-local Ollama. "
            "Set EVIDEX_ALLOW_REMOTE_OLLAMA=1 only for a trusted LAN host."
        )

    if not model:
        raise ValueError("Evidex Ollama model must be configured.")

    return LlmConfig(
        api_key="ollama-local",
        model=model,
        base_url=base_url,
    )


class LlmClient:
    def __init__(self, cfg: LlmConfig):
        self._cfg = cfg
        self._client = None
        self.last_error: str | None = None

    @property
    def enabled(self) -> bool:
        if _env_truthy("LLM_DISABLED", "0"):
            return False
        return bool(self._cfg.api_key and self._cfg.base_url and self._cfg.model)

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not self.enabled:
            return None

        from openai import OpenAI  # local Ollama protocol client only

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
            response = client.chat.completions.create(
                model=(model or self._cfg.model),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
            )
            return (response.choices[0].message.content or "").strip()
        except Exception as exc:
            try:
                self.last_error = f"{type(exc).__name__}: {exc}"
            except Exception:
                self.last_error = type(exc).__name__
            return ""
