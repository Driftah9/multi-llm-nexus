"""Config schema validation for providers.yaml and adapters.yaml.

Validates at boot to catch common config mistakes (missing fields, wrong types,
invalid tier names) before they cause runtime failures. Uses Pydantic v2.
"""
from typing import Any, Dict, Optional, List, Literal, Union
from pydantic import BaseModel, Field, field_validator, model_validator
import logging

logger = logging.getLogger("nexus.config_schema")


class ProviderConfig(BaseModel):
    """Single provider entry in providers.yaml."""
    type: str  # openai, cohere, cloudflare, etc.
    model: str
    enabled: Optional[bool] = True
    priority: Optional[int] = None
    tier: Optional[str] = None
    display_prefix: Optional[str] = None
    model_display: Optional[str] = None
    effort_levels: Optional[bool] = False
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    access_tier: Optional[str] = None
    rpm: Optional[Union[int, None]] = None
    rpd: Optional[Union[int, None]] = None
    tpm: Optional[Union[int, None]] = None
    tpd: Optional[Union[int, None]] = None
    context_window: Optional[int] = None
    max_tokens_input: Optional[int] = None
    max_tokens_output: Optional[int] = None
    specialized: Optional[bool] = False
    domains: Optional[List[str]] = None
    capabilities: Optional[List[str]] = None
    api_token: Optional[str] = None
    account_id: Optional[str] = None
    neurons_per_day: Optional[int] = None
    pricing: Optional[Dict[str, float]] = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        allowed = {"openai", "cohere", "cloudflare", "huggingface", "deepinfra", "anthropic"}
        if v not in allowed:
            raise ValueError(f"type must be one of {allowed}, got {v}")
        return v

    @field_validator("tier")
    @classmethod
    def validate_tier(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {"nano", "standard", "deep", "apex"}
        if v not in allowed:
            raise ValueError(f"tier must be one of {allowed}, got {v}")
        return v

    @field_validator("access_tier")
    @classmethod
    def validate_access_tier(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {"free", "trial", "paid", "unlimited", "credits", "mixed"}
        if v not in allowed:
            raise ValueError(f"access_tier must be one of {allowed}, got {v}")
        return v


class RoutingPattern(BaseModel):
    """Single routing pattern in providers.yaml → routing → patterns."""
    match: str
    provider: Optional[str] = None
    providers: Optional[List[str]] = None
    reason: Optional[str] = None
    fallback: Optional[str] = None

    @model_validator(mode="after")
    def check_provider_or_providers(self):
        if not self.provider and not self.providers:
            raise ValueError("pattern must have either 'provider' or 'providers'")
        if self.provider and self.providers:
            raise ValueError("pattern cannot have both 'provider' and 'providers'")
        return self


class FailoverChain(BaseModel):
    """Failover strategy and health check config."""
    strategy: Literal["priority", "round_robin", "least_loaded"] = "priority"
    health_check_interval: Optional[int] = 60
    retry_attempts: Optional[int] = 3
    retry_delay: Optional[float] = 0.5
    failure_threshold: Optional[int] = 3
    cooldown_seconds: Optional[float] = 30.0
    on_failure: Literal["next_available", "random", "fallback_only"] = "next_available"
    enable_health_monitoring: Optional[bool] = True
    chains: Optional[Dict[str, List[str]]] = None


class RoutingConfig(BaseModel):
    """Routing section of providers.yaml."""
    default: Optional[str] = None
    triage: Optional[str] = None
    patterns: Optional[List[RoutingPattern]] = None
    tier_defaults: Optional[Dict[str, str]] = None
    load_balance: Optional[Dict[str, Any]] = None


class ProvidersYaml(BaseModel):
    """Top-level structure of providers.yaml."""
    providers: Dict[str, ProviderConfig]
    routing: Optional[RoutingConfig] = None
    failover: Optional[FailoverChain] = None

    @field_validator("providers")
    @classmethod
    def validate_providers_not_empty(cls, v: Dict[str, ProviderConfig]) -> Dict[str, ProviderConfig]:
        if not v:
            raise ValueError("providers must have at least one entry")
        return v


class AdapterConfig(BaseModel):
    """Generic adapter config entry."""
    enabled: Optional[bool] = True
    # Additional fields are allowed (adapter-specific)

    class Config:
        extra = "allow"


class AdaptersYaml(BaseModel):
    """Top-level structure of adapters.yaml."""
    bot_name: Optional[str] = "Nexus"
    system_prompt: Optional[str] = None
    channel_map: Optional[Dict[str, str]] = None
    workdir: Optional[str] = None
    mattermost: Optional[AdapterConfig] = None
    discord: Optional[AdapterConfig] = None
    telegram: Optional[AdapterConfig] = None
    slack: Optional[AdapterConfig] = None
    matrix: Optional[AdapterConfig] = None
    openai_api: Optional[AdapterConfig] = None

    class Config:
        extra = "allow"  # Allow unknown adapters


def validate_providers_yaml(config: Dict[str, Any]) -> ProvidersYaml:
    """Validate providers.yaml against schema. Raises ValidationError on failure."""
    try:
        return ProvidersYaml(**config)
    except Exception as e:
        raise ValueError(f"providers.yaml validation failed: {e}") from e


def validate_adapters_yaml(config: Dict[str, Any]) -> AdaptersYaml:
    """Validate adapters.yaml against schema. Raises ValidationError on failure."""
    try:
        return AdaptersYaml(**config)
    except Exception as e:
        raise ValueError(f"adapters.yaml validation failed: {e}") from e
