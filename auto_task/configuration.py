"""Versioned provider configuration; never return stored credentials to callers."""

import json
import os
import ipaddress
from typing import Dict
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from fastapi import HTTPException
from pydantic import BaseModel, Field, validator

from .entities import AiSetting
from .store import dumps


class ModelOverride(BaseModel):
    model: str = Field(..., min_length=1, max_length=128)
    reasoning_effort: str = "medium"
    input_usd_per_million: float = Field(..., ge=0)
    cached_usd_per_million: float = Field(..., ge=0)
    output_usd_per_million: float = Field(..., ge=0)


class ProviderConfig(BaseModel):
    provider: str = "codex"
    base_url: str = Field(..., min_length=1, max_length=2048)
    model: str = Field(..., min_length=1, max_length=128)
    reasoning_effort: str = "medium"
    input_usd_per_million: float = Field(0, ge=0)
    cached_usd_per_million: float = Field(0, ge=0)
    output_usd_per_million: float = Field(0, ge=0)
    max_concurrency: int = Field(4, ge=1, le=64)
    per_user_concurrency: int = Field(1, ge=1, le=8)
    max_queued_per_user: int = Field(1000, ge=1, le=10000)
    agent_concurrency: int = Field(4, ge=1, le=64)
    sandbox_concurrency: int = Field(2, ge=1, le=16)
    document_concurrency: int = Field(2, ge=1, le=16)
    max_task_cost_usd: float = Field(2, gt=0, le=100)
    max_output_tokens: int = Field(8192, ge=128, le=65536)
    max_prompt_bytes: int = Field(200000, ge=1024, le=2000000)
    monthly_user_budget_usd: float = Field(0, ge=0, le=100000)
    monthly_course_budget_usd: float = Field(0, ge=0, le=100000)
    timeout_seconds: int = Field(480, ge=10, le=1800)
    max_storage_mb: int = Field(512, ge=1, le=102400)
    model_overrides: Dict[str, ModelOverride] = Field(default_factory=dict)


    @validator("model_overrides")
    def validate_overrides(cls, value):
        import math
        if set(value) - {"moderation", "grading", "authoring", "tutor"}:
            raise ValueError("Unsupported model purpose")
        for policy in value.values():
            cls.effort_supported(policy.reasoning_effort)
            if not all(math.isfinite(getattr(policy, field)) for field in ("input_usd_per_million", "cached_usd_per_million", "output_usd_per_million")):
                raise ValueError("Override price must be finite")
        return value

    @validator("provider")
    def provider_supported(cls, value):
        if value not in ("codex", "chat_completions"):
            raise ValueError("Unsupported provider")
        return value

    @validator("base_url")
    def safe_url(cls, value):
        url = urlsplit(value)
        if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Expected a provider API base URL without credentials/query")
        if url.scheme == "http":
            from const import MOD
            try:
                loopback = ipaddress.ip_address(url.hostname).is_loopback
            except ValueError:
                loopback = url.hostname.lower() == "localhost"
            if MOD != "debug" or not loopback:
                raise ValueError("Provider API must use HTTPS unless it is loopback development")
        return value.rstrip("/")

    @validator("reasoning_effort")
    def effort_supported(cls, value):
        if value not in ("none", "minimal", "low", "medium", "high", "xhigh"):
            raise ValueError("Unsupported reasoning effort")
        return value

    @validator("input_usd_per_million", "cached_usd_per_million", "output_usd_per_million", "monthly_user_budget_usd", "monthly_course_budget_usd")
    def finite_price(cls, value):
        import math
        if not math.isfinite(value):
            raise ValueError("Price must be finite")
        return value


def cipher():
    key = os.environ.get("AUTOLAB_AI_SECRET_KEY")
    if not key:
        raise HTTPException(503, "AUTOLAB_AI_SECRET_KEY is not configured")
    try:
        return Fernet(key.encode())
    except (TypeError, ValueError) as error:
        raise HTTPException(503, "AUTOLAB_AI_SECRET_KEY is invalid") from error


def save_provider(session, config, api_key=None, expected_version=None):
    row = session.query(AiSetting).filter_by(id="runtime").with_for_update().one()
    if expected_version is not None and expected_version != row.version:
        raise HTTPException(409, "AI provider settings version conflict")
    if api_key is not None and (not api_key or api_key != api_key.strip()):
        raise HTTPException(400, "Provider API key must be non-empty and have no surrounding whitespace")
    if row.secret:
        try:
            previous = ProviderConfig.parse_obj(json.loads(row.data))
        except (TypeError, ValueError):
            previous = None
        endpoint_changed = previous is None or previous.provider != config.provider or previous.base_url != config.base_url
        if endpoint_changed and api_key is None:
            raise HTTPException(409, "Changing provider endpoint requires a new API key")
    row.data = dumps(config.dict())
    if api_key is not None:
        row.secret = cipher().encrypt(api_key.encode()).decode()
    row.version += 1
    return public_settings(row)


def public_settings(row):
    value = json.loads(row.data)
    allowed = set(ProviderConfig.__fields__)
    return {**{key: item for key, item in value.items() if key in allowed},
            "version": row.version, "has_api_key": bool(row.secret)}


def provider(session, purpose=None):
    row = session.query(AiSetting).filter_by(id="runtime").one_or_none()
    if not row or not row.secret:
        raise HTTPException(503, "Configure an AI provider before running tasks")
    config = ProviderConfig.parse_obj(json.loads(row.data))
    override = config.model_overrides.get(purpose)
    if override:
        config = config.copy(update=override.dict())
    try:
        key = cipher().decrypt(row.secret.encode()).decode()
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(503, "Stored AI provider credential cannot be decrypted") from error
    return config, key


def data_root():
    return Path(os.environ.get("AUTOLAB_AI_DATA_DIR", str(Path(__file__).resolve().parents[1] / "data" / "auto_task"))).resolve()


def safe_path(relative):
    root = data_root()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise HTTPException(400, "Invalid storage path")
    return path
