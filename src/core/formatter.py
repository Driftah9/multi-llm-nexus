"""
Platform-aware output formatting for claude-brain.

Same content, different rendering per platform. Each platform has
different markdown flavors, message size limits, and rich features.

This module ensures Claude's responses look native on every platform
without the engine or behavioral layer knowing which platform it's on.

Usage:
    fmt = PlatformFormatter("discord")
    chunks = fmt.format_response(long_response_text)
    status = fmt.format_status(model="Opus", effort="high", elapsed=45)
"""

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class PlatformLimits:
    """Character/formatting limits per platform."""
    max_message_length: int     # Max chars per message
    supports_markdown: bool     # Basic markdown (*bold*, `code`)
    supports_code_blocks: bool  # Triple-backtick blocks
    supports_tables: bool       # Markdown tables
    supports_embeds: bool       # Rich embeds (Discord)
    supports_blocks: bool       # Block Kit (Slack)
    supports_threads: bool      # Threaded replies
    supports_reactions: bool    # Emoji reactions
    code_block_syntax: str      # "```" or platform-specific


PLATFORM_LIMITS: Dict[str, PlatformLimits] = {
    "telegram": PlatformLimits(
        max_message_length=4096,
        supports_markdown=True,
        supports_code_blocks=True,
        supports_tables=False,      # Telegram renders tables poorly
        supports_embeds=False,
        supports_blocks=False,
        supports_threads=True,      # Via topic threads
        supports_reactions=True,
        code_block_syntax="```",
    ),
    "discord": PlatformLimits(
        max_message_length=2000,
        supports_markdown=True,
        supports_code_blocks=True,
        supports_tables=False,      # Discord renders tables poorly
        supports_embeds=True,       # Rich embeds
        supports_blocks=False,
        supports_threads=True,      # Forum threads
        supports_reactions=True,
        code_block_syntax="```",
    ),
    "mattermost": PlatformLimits(
        max_message_length=16383,
        supports_markdown=True,
        supports_code_blocks=True,
        supports_tables=True,       # Full GFM tables
        supports_threads=True,      # Thread replies
        supports_embeds=False,
        supports_blocks=False,
        supports_reactions=True,
        code_block_syntax="```",
    ),
    "slack": PlatformLimits(
        max_message_length=4000,    # mrkdwn limit
        supports_markdown=False,    # Uses mrkdwn (different syntax)
        supports_code_blocks=True,
        supports_tables=False,
        supports_embeds=False,
        supports_blocks=True,       # Block Kit
        supports_threads=True,
        supports_reactions=True,
        code_block_syntax="```",
    ),
}


class PlatformFormatter:
    """Formats output for a specific platform.

    Handles:
      - Message splitting (respects platform max length)
      - Markdown translation (GFM -> platform-specific)
      - Status message formatting
      - Table rendering (or fallback for platforms that don't support them)
    """

    def __init__(self, platform: str):
        self.platform = platform
        self.limits = PLATFORM_LIMITS.get(platform, PLATFORM_LIMITS["mattermost"])

    def format_response(self, text: str) -> List[str]:
        """Split and format a response for the platform.

        Returns a list of message chunks, each within the platform's
        character limit. Tries to split at natural boundaries (paragraphs,
        code blocks, sentences).
        """
        if not text:
            return [""]

        text = self._adapt_markdown(text)
        max_len = self.limits.max_message_length

        if len(text) <= max_len:
            return [text]

        return self._split_at_boundaries(text, max_len)

    def format_status(self, model: str, effort: str, elapsed: int = 0, label: str = "thinking") -> str:
        """Format the 'thinking...' status message.

        All platforms show model + effort. Elapsed time optional.
        """
        time_str = ""
        if elapsed > 0 and self.limits.supports_markdown:
            mins, secs = divmod(elapsed, 60)
            time_str = f" - {mins}:{secs:02d}"

        if self.platform == "slack":
            return f"_{label}... ({model} - {effort}){time_str}_"
        else:
            return f"{label}... ({model} - {effort}){time_str}"

    def format_help(self, help_table: str) -> str:
        """Adapt help text for the platform."""
        if self.platform == "slack":
            return self._markdown_to_mrkdwn(help_table)
        if not self.limits.supports_tables:
            return self._table_to_list(help_table)
        return help_table

    def format_error(self, error: str) -> str:
        """Format an error message."""
        if self.platform == "slack":
            return f":warning: {error}"
        return f"Error: {error}"

    def _adapt_markdown(self, text: str) -> str:
        """Convert standard markdown to platform-specific format."""
        if self.platform == "slack":
            return self._markdown_to_mrkdwn(text)
        if self.platform == "telegram":
            return self._markdown_to_telegram(text)
        return text

    def _markdown_to_mrkdwn(self, text: str) -> str:
        """Convert GFM markdown to Slack mrkdwn syntax."""
        # Bold: **text** -> *text*
        import re
        text = re.sub(r'\*\*(.+?)\*\*', r'*\1*', text)
        # Italic: *text* -> _text_ (but avoid converting bold)
        # Links stay the same: [text](url) -> <url|text>
        text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<\2|\1>', text)
        return text

    def _markdown_to_telegram(self, text: str) -> str:
        """Adjust markdown for Telegram's MarkdownV2 parser."""
        # Telegram mostly handles standard markdown, but has quirks
        # with special characters that need escaping
        return text

    def _table_to_list(self, text: str) -> str:
        """Convert markdown tables to bullet lists for platforms without table support."""
        import re
        lines = text.split("\n")
        result = []
        in_table = False
        headers = []

        for line in lines:
            if "|" in line and not line.strip().startswith("```"):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if all(c.replace("-", "").replace(":", "") == "" for c in cells):
                    continue  # Skip separator row
                if not in_table:
                    headers = cells
                    in_table = True
                else:
                    # Format as "- **header**: value"
                    parts = []
                    for i, cell in enumerate(cells):
                        if i < len(headers) and headers[i]:
                            parts.append(f"**{headers[i]}**: {cell}")
                    result.append("- " + " | ".join(parts))
            else:
                in_table = False
                headers = []
                result.append(line)

        return "\n".join(result)

    def _split_at_boundaries(self, text: str, max_len: int) -> List[str]:
        """Split text at natural boundaries (paragraphs, then sentences)."""
        chunks = []
        remaining = text

        while len(remaining) > max_len:
            # Try to split at double newline (paragraph)
            split_at = remaining.rfind("\n\n", 0, max_len)
            if split_at == -1 or split_at < max_len // 2:
                # Try single newline
                split_at = remaining.rfind("\n", 0, max_len)
            if split_at == -1 or split_at < max_len // 2:
                # Try space (word boundary)
                split_at = remaining.rfind(" ", 0, max_len)
            if split_at == -1:
                # Hard split
                split_at = max_len

            chunks.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()

        if remaining:
            chunks.append(remaining)

        return chunks
