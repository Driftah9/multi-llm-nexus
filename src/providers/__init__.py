from .base import BaseProvider, Message, ProviderResponse, ToolCall, ToolResult
from .claude_code import ClaudeCodeProvider
from .openai import OpenAIProvider
from .ollama import OllamaProvider
from .anthropic import AnthropicProvider
from .gemini import GeminiProvider
from .bedrock import BedrockProvider
from .cohere import CohereProvider

PROVIDER_REGISTRY = {
    "claude_code":  ClaudeCodeProvider,
    "anthropic":    AnthropicProvider,
    "openai":       OpenAIProvider,
    "ollama":       OllamaProvider,
    "gemini":       GeminiProvider,
    "vertex_ai":    GeminiProvider,    # same class, different config path
    "bedrock":      BedrockProvider,
    "cohere":       CohereProvider,
    # OpenAI-compatible aliases — all use OpenAIProvider with base_url from config
    "groq":         OpenAIProvider,
    "mistral":      OpenAIProvider,
    "deepseek":     OpenAIProvider,
    "xai":          OpenAIProvider,
    "together":     OpenAIProvider,
    "fireworks":    OpenAIProvider,
    "perplexity":   OpenAIProvider,
    "huggingface":  OpenAIProvider,
    "cerebras":     OpenAIProvider,
    "azure_openai": OpenAIProvider,
    "lm_studio":    OpenAIProvider,
    "vllm":         OpenAIProvider,
}


def load_provider(provider_type: str, config: dict) -> BaseProvider:
    cls = PROVIDER_REGISTRY.get(provider_type)
    if not cls:
        raise ValueError(
            f"Unknown provider type: '{provider_type}'. "
            f"Available: {sorted(PROVIDER_REGISTRY)}"
        )
    return cls(config)
