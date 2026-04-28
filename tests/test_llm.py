from __future__ import annotations

from types import SimpleNamespace

import pytest

from storyforge.config import LlmConfig, MODEL_ROUTING, _parse_model_routing, load_llm_config
from storyforge.llm.client import LlmClient, LlmSkipError


def test_load_llm_config_uses_provider_specific_openai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STORYFORGE_API_KEY", raising=False)
    monkeypatch.delenv("STORYFORGE_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("STORYFORGE_SKIP_LLM", "0")
    monkeypatch.setenv("STORYFORGE_LLM_PROVIDER", "openai")
    monkeypatch.setenv("STORYFORGE_OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("STORYFORGE_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("STORYFORGE_BASE_URL", "https://api.openai.com/v1")

    config = load_llm_config()

    assert config.provider == "openai"
    assert config.api_key == "openai-key"
    assert config.model == "gpt-4o-mini"
    assert config.base_url == "https://api.openai.com/v1"


def test_load_llm_config_uses_provider_specific_anthropic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STORYFORGE_API_KEY", raising=False)
    monkeypatch.delenv("STORYFORGE_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("STORYFORGE_SKIP_LLM", "0")
    monkeypatch.setenv("STORYFORGE_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("STORYFORGE_ANTHROPIC_API_KEY", "anthropic-key")

    config = load_llm_config()

    assert config.provider == "anthropic"
    assert config.api_key == "anthropic-key"


def test_llm_client_skip_llm_raises() -> None:
    with pytest.raises(LlmSkipError):
        LlmClient(LlmConfig(skip_llm=True))


def test_llm_client_anthropic_generate_text_tracks_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAnthropicClient:
        def __init__(self, api_key: str) -> None:
            assert api_key == "anthropic-key"
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **kwargs: object) -> object:
            assert kwargs["model"] == "claude-test"
            return SimpleNamespace(
                content=[SimpleNamespace(text="anthropic output")],
                usage=SimpleNamespace(input_tokens=12, output_tokens=8),
            )

    monkeypatch.setattr("storyforge.llm.client.anthropic.Anthropic", FakeAnthropicClient)

    client = LlmClient(LlmConfig(provider="anthropic", api_key="anthropic-key", model="claude-test"))

    result = client.generate_text([{"role": "user", "content": "hello"}], max_tokens=64)

    assert result == "anthropic output"
    usage = client.get_total_usage()
    assert usage.input_tokens == 12
    assert usage.output_tokens == 8
    assert usage.total_tokens == 20


def test_llm_client_openai_generate_json_tracks_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [{"message": {"content": '{"title": "OpenAI Result"}'}}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 5, "total_tokens": 14},
            }

    class FakeHttpxClient:
        def __init__(self, *, base_url: str, headers: dict[str, str], timeout: float) -> None:
            assert base_url == "https://api.openai.com/v1"
            assert headers["Authorization"] == "Bearer openai-key"
            assert timeout == 30.0

        def post(self, path: str, json: dict[str, object]) -> FakeResponse:
            assert path == "/chat/completions"
            assert json["model"] == "gpt-4o-mini"
            return FakeResponse()

    monkeypatch.setattr("storyforge.llm.client.httpx.Client", FakeHttpxClient)

    client = LlmClient(
        LlmConfig(
            provider="openai",
            api_key="openai-key",
            model="gpt-4o-mini",
            base_url="https://api.openai.com/v1",
        )
    )

    result = client.generate_json([{"role": "user", "content": "hello"}], max_tokens=64)

    assert result == {"title": "OpenAI Result"}
    usage = client.get_total_usage()
    assert usage.input_tokens == 9
    assert usage.output_tokens == 5
    assert usage.total_tokens == 14


def test_model_routing_returns_correct_model_for_task_type() -> None:
    """Verify that resolve_model returns the routed model for known task types."""
    config = LlmConfig(
        provider="anthropic",
        api_key="test-key",
        model="claude-sonnet-4-6-20250514",
        model_routing={
            "brief": "claude-haiku-4-5-20251001",
            "chapter": "claude-opus-4-6-20250514",
        },
    )
    assert config.resolve_model("brief") == "claude-haiku-4-5-20251001"
    assert config.resolve_model("chapter") == "claude-opus-4-6-20250514"
    # Unknown task type falls back to default
    assert config.resolve_model("unknown") == "claude-sonnet-4-6-20250514"


def test_model_routing_default_routing() -> None:
    """Verify that LlmConfig without model_routing uses MODULE_ROUTING defaults."""
    config = LlmConfig(provider="anthropic", api_key="test-key")
    assert config.model_routing == MODEL_ROUTING
    assert config.resolve_model("brief") == MODEL_ROUTING["brief"]


def test_parse_model_routing() -> None:
    """Verify _parse_model_routing parses env var format correctly."""
    result = _parse_model_routing("brief=haiku,chapter=opus,review=sonnet")
    assert result == {"brief": "haiku", "chapter": "opus", "review": "sonnet"}

    # Empty string returns empty dict
    assert _parse_model_routing("") == {}

    # Malformed pairs are ignored
    result = _parse_model_routing("invalid,bad=pair,also_invalid")
    assert result == {"bad": "pair"}


def test_load_llm_config_with_model_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that load_llm_config parses STORYFORGE_MODEL_ROUTING env var."""
    monkeypatch.delenv("STORYFORGE_API_KEY", raising=False)
    monkeypatch.delenv("STORYFORGE_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("STORYFORGE_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("STORYFORGE_SKIP_LLM", "0")
    monkeypatch.setenv("STORYFORGE_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("STORYFORGE_MODEL_ROUTING", "brief=haiku,chapter=opus")

    config = load_llm_config()
    assert config.resolve_model("brief") == "haiku"
    assert config.resolve_model("chapter") == "opus"
    assert config.resolve_model("review") == MODEL_ROUTING["review"]
