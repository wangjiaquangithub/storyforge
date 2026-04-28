from __future__ import annotations

import os


# Default model routing: maps task types to recommended models
MODEL_ROUTING: dict[str, str] = {
    "brief": "claude-sonnet-4-6-20250514",
    "outline": "claude-sonnet-4-6-20250514",
    "chapter": "claude-sonnet-4-6-20250514",
    "chapter_rewrite": "claude-sonnet-4-6-20250514",
    "review": "claude-sonnet-4-6-20250514",
}


class LlmConfig:
    def __init__(
        self,
        *,
        provider: str = "anthropic",
        api_key: str = "",
        model: str = "claude-sonnet-4-6-20250514",
        max_tokens: int = 8192,
        temperature: float = 0.7,
        skip_llm: bool = False,
        base_url: str = "",
        timeout_seconds: float = 30.0,
        model_routing: dict[str, str] | None = None,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.skip_llm = skip_llm
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.model_routing = model_routing or dict(MODEL_ROUTING)

    def resolve_model(self, task_type: str) -> str:
        """Return the model for the given task type, falling back to default."""
        return self.model_routing.get(task_type, self.model)


def _parse_model_routing(raw: str) -> dict[str, str]:
    """Parse 'brief=model1,chapter=model2' into dict."""
    routing: dict[str, str] = {}
    for pair in raw.split(","):
        if "=" in pair:
            task_type, model = pair.split("=", 1)
            routing[task_type.strip()] = model.strip()
    return routing


class StorageConfig:
    def __init__(
        self,
        *,
        backend: str = "sqlite",
        db_path: str = "",
        s3_bucket: str = "",
        s3_prefix: str = "",
        s3_region: str = "",
        s3_endpoint_url: str = "",
        s3_access_key_id: str = "",
        s3_secret_access_key: str = "",
    ) -> None:
        self.backend = backend
        self.db_path = db_path
        self.s3_bucket = s3_bucket
        self.s3_prefix = s3_prefix
        self.s3_region = s3_region
        self.s3_endpoint_url = s3_endpoint_url
        self.s3_access_key_id = s3_access_key_id
        self.s3_secret_access_key = s3_secret_access_key


def load_storage_config(default_db_path: str) -> StorageConfig:
    backend = os.environ.get("STORYFORGE_STORAGE_BACKEND", "sqlite")
    return StorageConfig(
        backend=backend,
        db_path=default_db_path,
        s3_bucket=os.environ.get("STORYFORGE_STORAGE_S3_BUCKET", ""),
        s3_prefix=os.environ.get("STORYFORGE_STORAGE_S3_PREFIX", ""),
        s3_region=os.environ.get("STORYFORGE_STORAGE_S3_REGION", ""),
        s3_endpoint_url=os.environ.get("STORYFORGE_STORAGE_S3_ENDPOINT_URL", ""),
        s3_access_key_id=os.environ.get("STORYFORGE_STORAGE_S3_ACCESS_KEY_ID", ""),
        s3_secret_access_key=os.environ.get("STORYFORGE_STORAGE_S3_SECRET_ACCESS_KEY", ""),
    )


def load_llm_config() -> LlmConfig:
    skip = os.environ.get("STORYFORGE_SKIP_LLM", "0") == "1"
    provider = os.environ.get("STORYFORGE_LLM_PROVIDER", "anthropic")
    api_key = os.environ.get("STORYFORGE_API_KEY", "")
    if not api_key:
        if provider == "openai":
            api_key = os.environ.get("STORYFORGE_OPENAI_API_KEY", "")
        elif provider == "anthropic":
            api_key = os.environ.get("STORYFORGE_ANTHROPIC_API_KEY", "")
    routing_env = os.environ.get("STORYFORGE_MODEL_ROUTING", "")
    routing = _parse_model_routing(routing_env) if routing_env else dict(MODEL_ROUTING)
    return LlmConfig(
        provider=provider,
        api_key=api_key,
        model=os.environ.get("STORYFORGE_MODEL", "claude-sonnet-4-6-20250514"),
        max_tokens=int(os.environ.get("STORYFORGE_MAX_TOKENS", "8192")),
        temperature=float(os.environ.get("STORYFORGE_TEMPERATURE", "0.7")),
        skip_llm=skip,
        base_url=os.environ.get("STORYFORGE_BASE_URL", ""),
        timeout_seconds=float(os.environ.get("STORYFORGE_TIMEOUT_SECONDS", "30.0")),
        model_routing=routing,
    )
