"""
Example: Using ProviderChain for hierarchical failover

This shows how to set up a multi-provider bridge with automatic failover:
1. Try Claude (primary)
2. If Claude fails, try Gemini (secondary)
3. If Gemini fails, try local Ollama (tertiary)

The system keeps working regardless of which provider is available.
"""
import asyncio
from multi_llm_nexus.src.providers.anthropic import AnthropicProvider
from multi_llm_nexus.src.providers.gemini import GeminiProvider
from multi_llm_nexus.src.providers.ollama import OllamaProvider
from multi_llm_nexus.src.core.provider_chain import (
    ProviderChain,
    ProviderChainEntry,
    ChainConfig,
)
from multi_llm_nexus.src.core.bridge import NexusBridge
from multi_llm_nexus.src.core.session import SessionStore


async def example_failover():
    """
    Set up a 3-provider chain and invoke with automatic failover.
    """

    # 1. Create provider instances
    claude = AnthropicProvider(
        model="claude-sonnet-4-6",
        api_key="YOUR_ANTHROPIC_API_KEY",
    )

    gemini = GeminiProvider(
        model="gemini-1.5-pro",
        api_key="YOUR_GOOGLE_API_KEY",
    )

    ollama = OllamaProvider(
        model="llama3.2:8b",
        endpoint="http://localhost:11434",
    )

    # 2. Create chain entries with priority
    entries = [
        ProviderChainEntry(
            provider=claude,
            priority=1,  # Primary
            tier="standard",
            name="claude_sonnet",
        ),
        ProviderChainEntry(
            provider=gemini,
            priority=2,  # Secondary
            tier="standard",
            name="gemini_pro",
        ),
        ProviderChainEntry(
            provider=ollama,
            priority=3,  # Tertiary (always available)
            tier="nano",
            name="ollama_local",
        ),
    ]

    # 3. Create chain config
    config = ChainConfig(
        strategy="priority",
        health_check_interval=60,
        retry_attempts=2,
        retry_delay=0.5,
        failure_threshold=3,
        on_failure="next_available",
        enable_health_monitoring=True,
    )

    # 4. Build the chain
    chain = ProviderChain(entries=entries, config=config)

    # 5. Create bridge with chain (not router)
    sessions = SessionStore()
    bridge = NexusBridge(chain=chain, sessions=sessions)

    # 6. Start health monitoring (background checks)
    await bridge.start_health_monitoring()

    try:
        # 7. Invoke — automatically tries primary, secondary, tertiary in order
        result = await bridge.invoke(
            prompt="Explain quantum computing in simple terms.",
            session_key="demo_session",
            tier="standard",
        )

        print(f"✓ Response received from: {result.provider_type}")
        print(f"  Cost: ${result.cost_usd:.4f}")
        print(f"  Elapsed: {result.elapsed:.2f}s")
        print(f"  Output: {result.text[:100]}...")

        # 8. Check which provider ended up handling it
        status = await bridge.get_provider_status()
        print(f"\nProvider Status:")
        for provider_name, status_info in status.items():
            print(f"  {provider_name}: {status_info['health']} "
                  f"({status_info['consecutive_failures']} failures)")

    finally:
        # 9. Stop health monitoring on shutdown
        await bridge.stop_health_monitoring()


async def example_with_failover_simulation():
    """
    Simulate a failure scenario where Claude times out,
    and the system automatically falls back to Gemini.
    """

    class FailingProvider:
        """Mock provider that always fails."""
        async def health_check(self):
            return False

        async def send(self, messages, system=""):
            raise TimeoutError("Provider timeout (simulated)")

    failing_claude = FailingProvider()

    gemini = GeminiProvider(
        model="gemini-1.5-pro",
        api_key="YOUR_GOOGLE_API_KEY",
    )

    entries = [
        ProviderChainEntry(
            provider=failing_claude,
            priority=1,
            tier="standard",
            name="claude_failing",
        ),
        ProviderChainEntry(
            provider=gemini,
            priority=2,
            tier="standard",
            name="gemini_fallback",
        ),
    ]

    config = ChainConfig(retry_attempts=2, failure_threshold=1)
    chain = ProviderChain(entries=entries, config=config)

    sessions = SessionStore()
    bridge = NexusBridge(chain=chain, sessions=sessions)

    # This should fail on Claude, then succeed on Gemini
    result = await bridge.invoke(
        prompt="What is the capital of France?",
        session_key="failover_test",
    )

    print(f"Result after failover:")
    print(f"  Primary failed, used: {result.provider_type}")
    print(f"  Success: {bool(result.text and 'error' not in result.text.lower())}")


if __name__ == "__main__":
    print("=" * 60)
    print("Example 1: Basic Failover Setup")
    print("=" * 60)
    asyncio.run(example_failover())

    print("\n" + "=" * 60)
    print("Example 2: Simulated Failover")
    print("=" * 60)
    asyncio.run(example_with_failover_simulation())
