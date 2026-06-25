"""Research worker — zero-token-on-cache-hit web pipeline.

Flow:
  1. Check cache (instant hit if valid)
  2. Search via duckduckgo-search (free, no API key)
  3. Fetch + extract pages LOCALLY (httpx + trafilatura on-box — no third-party reader)
  4. Synthesize via the configured triage model (default: haiku-class)
  5. Auto-cache result with TTL

When cache hits: zero token cost.
When scraping: only the synthesis step costs tokens (no agent spawning).

Config:
  NEXUS_TRIAGE_MODEL — model alias passed to the triage synthesis call (default: haiku)
  NEXUS_TRIAGE_BIN   — path to the claude CLI binary (auto-detected via PATH if unset)
"""

import asyncio
import logging
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from .cache_manager import get_cache

logger = logging.getLogger(__name__)

_TRIAGE_MODEL = os.environ.get("NEXUS_TRIAGE_MODEL", "haiku")
_TRIAGE_BIN = os.environ.get("NEXUS_TRIAGE_BIN", "")


def _find_claude_bin() -> str:
    """Locate the claude CLI. Explicit env var wins; otherwise search PATH."""
    if _TRIAGE_BIN:
        return _TRIAGE_BIN
    found = shutil.which("claude")
    if found:
        return found
    raise RuntimeError(
        "claude CLI not found. Set NEXUS_TRIAGE_BIN or ensure 'claude' is on PATH."
    )


async def _search_duckduckgo(query: str, num_results: int = 5) -> list[dict]:
    """Search via DDGS (free, no API key). Returns [{title, url, snippet}]."""
    try:
        from ddgs import DDGS
        client = DDGS()
        results = []
        for r in client.text(query, max_results=num_results):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("href", ""),   # DDGS uses 'href'
                "snippet": r.get("body", ""),
            })
        return results
    except ImportError:
        logger.warning("ddgs not installed. Install with: pip install ddgs")
        return []
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []


_FETCH_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


async def _fetch_page_local(url: str, timeout: int = 12) -> Optional[str]:
    """Fetch + extract a page's main content ENTIRELY ON-BOX.

    Was Jina Reader (r.jina.ai) — a remote third party that received the URL, the implied
    query intent, and the full page content before any local LLM saw it. Now httpx fetches
    the raw HTML directly and trafilatura extracts the main article text locally, so nothing
    about the page leaves the operator's machine until the (locally-routed) synthesis LLM.
    Local-first by construction — the floor needs no third-party reader.

    Returns clean text or None on failure. Requires `httpx` + `trafilatura`.
    """
    try:
        import httpx
        import trafilatura
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                     headers={"User-Agent": _FETCH_UA}) as client:
            r = await client.get(url)
            r.raise_for_status()
            html = r.text
        text = await asyncio.to_thread(
            trafilatura.extract, html,
            include_comments=False, include_tables=True, favor_recall=True, url=url,
        )
        return text or None
    except Exception as e:
        logger.warning(f"local fetch/parse failed for {url}: {e}")
        return None


async def _synthesize(query: str, pages: dict[str, str], sources: list[str]) -> str:
    """Synthesize research via the configured triage model (fast, cheap)."""
    truncated = {url: content[:1500] for url, content in pages.items()}
    pages_text = "\n\n---\n\n".join(
        f"## {url}\n\n{content}" for url, content in truncated.items()
    )
    synthesis_prompt = f"""Synthesize the following research pages into a concise markdown summary.

QUERY: {query}

PAGES:
{pages_text}

OUTPUT:
- 2-3 sentence overview
- Key findings (bullet list)
- Sources (with URLs)

Be concise. Extract the essential information needed to answer the original query."""

    try:
        claude_bin = _find_claude_bin()
        result = await asyncio.to_thread(
            subprocess.run,
            [
                claude_bin,
                "-p", synthesis_prompt,
                "--model", _TRIAGE_MODEL,
                "--strict-mcp-config",
                "--mcp-config", '{"mcpServers":{}}',
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "NEXUS_HOOK_CHILD": "1"},
        )
        if result.returncode == 0:
            return result.stdout.strip()
        logger.error(f"Synthesis failed (rc={result.returncode}): {result.stderr[:300]}")
    except Exception as e:
        logger.error(f"Synthesis error: {e}")

    return _fallback_summary(query, pages)


def _fallback_summary(query: str, pages: dict[str, str]) -> str:
    """Simple concatenation when synthesis fails."""
    summary = f"# Research: {query}\n\n## Pages Retrieved\n\n"
    for url, content in pages.items():
        summary += f"**Source:** {url}\n\n{content[:500]}\n\n"
    return summary


async def research(
    query: str,
    scope: str = "general",
    project_name: Optional[str] = None,
    num_results: int = 5,
    force_refresh: bool = False,
) -> str:
    """Execute the research pipeline.

    Args:
        query:          Research question
        scope:          "general" or "project"
        project_name:   Required if scope="project"
        num_results:    Number of web results to fetch
        force_refresh:  Bypass cache, force fresh research

    Returns:
        Markdown summary string
    """
    cache = get_cache(scope=scope, project_name=project_name)

    if not force_refresh:
        cached = cache.get_summary(query)
        if cached:
            logger.info(f"Cache hit: {query[:60]}")
            return cached

    logger.info(f"Researching: {query[:60]}")

    search_results = await _search_duckduckgo(query, num_results=num_results)
    if not search_results:
        return f"# Research Failed: {query}\n\nNo search results found."

    urls = [r["url"] for r in search_results]
    pages_raw = await asyncio.gather(
        *[_fetch_page_local(url) for url in urls],
        return_exceptions=True,
    )

    pages: dict[str, str] = {}
    valid_sources: list[str] = []
    for url, content in zip(urls, pages_raw):
        if isinstance(content, str) and content.strip():
            pages[url] = content
            valid_sources.append(url)
        elif isinstance(content, Exception):
            logger.warning(f"Fetch error for {url}: {content}")

    if not pages:
        return f"# Research Failed: {query}\n\nCould not fetch any pages."

    summary = await _synthesize(query, pages, valid_sources)
    cache.save_summary(query, summary, valid_sources, raw_pages=pages)
    return summary


async def research_with_context(
    query: str,
    scope: str = "general",
    project_name: Optional[str] = None,
) -> dict:
    """Research with metadata (sources, timestamp, cached flag)."""
    cache = get_cache(scope=scope, project_name=project_name)
    cached_summary = cache.get_summary(query)
    if cached_summary:
        return {
            "summary": cached_summary,
            "sources": [],
            "timestamp": datetime.now().isoformat(),
            "cached": True,
        }
    summary = await research(query, scope=scope, project_name=project_name)
    return {
        "summary": summary,
        "sources": [],
        "timestamp": datetime.now().isoformat(),
        "cached": False,
    }


def research_sync(
    query: str,
    scope: str = "general",
    project_name: Optional[str] = None,
) -> str:
    """Synchronous wrapper for contexts where async isn't available."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Can't block in a running loop — return cache only
            cache = get_cache(scope=scope, project_name=project_name)
            cached = cache.get_summary(query)
            if cached:
                return cached
            return "# Research Deferred\n\nAsync loop running. Retry on next message."
        return loop.run_until_complete(
            research(query, scope=scope, project_name=project_name)
        )
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(
                research(query, scope=scope, project_name=project_name)
            )
        finally:
            loop.close()
