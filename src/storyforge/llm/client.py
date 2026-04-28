from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import anthropic
import httpx

from storyforge.config import LlmConfig


@dataclass
class LlmUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def accumulate(self, other: "LlmUsage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.total_tokens += other.total_tokens


class LlmSkipError(Exception):
    """Raised when LLM calls are skipped (e.g., CI mode)."""


class LlmClient:
    def __init__(self, config: LlmConfig) -> None:
        self.config = config
        self.usage_history: list[LlmUsage] = []
        if config.skip_llm:
            raise LlmSkipError("LLM calls are skipped (STORYFORGE_SKIP_LLM=1)")
        if not config.api_key:
            raise ValueError("STORYFORGE_API_KEY is not set")
        if config.provider == "anthropic":
            self._client = anthropic.Anthropic(api_key=config.api_key)
        elif config.provider == "openai":
            base_url = config.base_url or "https://api.openai.com/v1"
            self._client = httpx.Client(
                base_url=base_url,
                headers={"Authorization": f"Bearer {config.api_key}"},
                timeout=config.timeout_seconds,
            )
        else:
            raise ValueError(f"Unsupported LLM provider: {config.provider}")

    def generate_json(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        text, usage = self._generate(messages, max_tokens=max_tokens)
        self.usage_history.append(usage)
        cleaned = _extract_json_block(text)
        return json.loads(cleaned)

    def generate_text(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
    ) -> str:
        text, usage = self._generate(messages, max_tokens=max_tokens)
        self.usage_history.append(usage)
        return text

    def get_total_usage(self) -> LlmUsage:
        total = LlmUsage()
        for u in self.usage_history:
            total.accumulate(u)
        return total

    def _generate(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
    ) -> tuple[str, LlmUsage]:
        if self.config.provider == "anthropic":
            return self._generate_anthropic(messages, max_tokens=max_tokens)
        if self.config.provider == "openai":
            return self._generate_openai(messages, max_tokens=max_tokens)
        raise ValueError(f"Unsupported LLM provider: {self.config.provider}")

    def _generate_anthropic(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
    ) -> tuple[str, LlmUsage]:
        max_t = max_tokens or self.config.max_tokens
        response = self._client.messages.create(
            model=self.config.model,
            max_tokens=max_t,
            temperature=self.config.temperature,
            messages=messages,
        )
        usage = LlmUsage(
            input_tokens=getattr(response.usage, "input_tokens", 0),
            output_tokens=getattr(response.usage, "output_tokens", 0),
            total_tokens=getattr(response.usage, "input_tokens", 0) + getattr(response.usage, "output_tokens", 0),
        )
        return response.content[0].text, usage

    def _generate_openai(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
    ) -> tuple[str, LlmUsage]:
        max_t = max_tokens or self.config.max_tokens
        response = self._client.post(
            "/chat/completions",
            json={
                "model": self.config.model,
                "messages": messages,
                "temperature": self.config.temperature,
                "max_tokens": max_t,
            },
        )
        response.raise_for_status()
        data = response.json()
        text = data["choices"][0]["message"]["content"]
        usage_data = data.get("usage", {})
        usage = LlmUsage(
            input_tokens=usage_data.get("prompt_tokens", 0),
            output_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )
        return text, usage


def _extract_json_block(text: str) -> str:
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        return text[start:end].strip()
    if "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        return text[start:end].strip()
    return text.strip()
