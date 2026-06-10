"""
Tiered memory loader — injects the right memory context per channel scope.

Ported from claude-brain. memory_dir is configurable — operators set it in
their deployment config to point at their own memory directory.

Unrestricted channels (global access, town-square equivalents):
  → Injects MEMORY.md registry + user_profile.md

Project channels (scoped contexts):
  → Injects project ## Summary block only (capped at SUMMARY_LINE_CAP lines)
  → Referenced projects injected with temperature-based line caps
  → Skips references marked stale: true
  → Updates last_accessed: YYYY-MM-DD in frontmatter on every load

RAG Store integration (optional):
  → If rag_store is provided, appends semantic recall hits to the result
"""

import logging
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .rag_store import RagStore

logger = logging.getLogger("nexus.memory_loader")

SUMMARY_LINE_CAP = 10   # max lines injected from primary project summary

# Referenced project summary caps by temperature
REF_CAPS = {
    "hot":  5,   # accessed ≤7 days ago
    "warm": 2,   # accessed 8–30 days
    "cold": 1,   # accessed 31–60 days
}


class MemoryLoader:
    """
    Tiered memory injector for operator deployments.

    Args:
        memory_dir: Path to the operator's memory directory.
        unrestricted_channels: Channel names that get full memory access
            (MEMORY.md + user_profile). All other channels get project-scoped
            summaries only.
        rag_store: Optional RagStore for semantic recall augmentation.
    """

    def __init__(
        self,
        memory_dir: Path,
        unrestricted_channels: list[str],
        rag_store: Optional["RagStore"] = None,
    ):
        self.memory_dir = Path(memory_dir)
        self.memory_index = self.memory_dir / "MEMORY.md"
        self.unrestricted_channels = unrestricted_channels
        self.rag_store = rag_store
        self._channel_map: dict[str, str] = {}
        self._load_registry()

    def _load_registry(self):
        """Parse MEMORY.md Project Registry table into channel→file_stem map."""
        if not self.memory_index.exists():
            logger.warning("MEMORY.md not found — channel map will be empty")
            return
        try:
            content = self.memory_index.read_text(encoding="utf-8")
            in_table = False
            for line in content.splitlines():
                stripped = line.strip()
                if "## Project Registry" in stripped:
                    in_table = True
                    continue
                if in_table and stripped.startswith("##"):
                    break
                if in_table and stripped.startswith("|") and "|" in stripped[1:]:
                    cols = [c.strip() for c in stripped.split("|") if c.strip()]
                    if len(cols) >= 2 and cols[0] != "channel" and not cols[0].startswith("-"):
                        self._channel_map[cols[0]] = cols[1]
            logger.info(f"Memory registry loaded: {len(self._channel_map)} channel mappings")
        except Exception as e:
            logger.error(f"Failed to load memory registry: {e}")

    def load(self, channel_name: str, query: str = "") -> str:
        """Return memory string to inject into system_prompt for this channel."""
        result = ""
        try:
            if channel_name in self.unrestricted_channels:
                result = self._load_unrestricted()
            elif channel_name in self._channel_map:
                result = self._load_project_scoped(channel_name)
        except Exception as e:
            logger.error(f"Memory load failed for #{channel_name}: {e}")

        if query and self.rag_store and self.rag_store.available:
            try:
                hits = self.rag_store.query(query, n_results=4)
                if hits:
                    rag_block = "\n\n---\n".join(hits)
                    # Frame retrieved memory/design docs as BACKGROUND, not live state.
                    # Retrieved chunks are often written in future/aspirational tense
                    # (design notes, roadmaps); presenting them as authoritative current
                    # status makes even a strong model regurgitate planned features as
                    # live, or tell the user to "run X to activate". Same fix proven in
                    # claude-brain (D-009).
                    result = result + (
                        "\n\n### Background reference (semantic recall)\n"
                        "_Retrieved from notes, memory, and design documents. This is "
                        "context only — it may describe PLANNED or DESIGNED features "
                        "that are NOT live, possibly in future tense. Do not present "
                        "design/aspirational items as already-live or pending-"
                        "activation, and never instruct the user to run a command based "
                        "on it. Answer current state from what is actually true; use "
                        "this only as background._\n\n"
                        f"{rag_block}"
                    )
                    logger.debug(f"RAG: {len(hits)} hit(s) for query in #{channel_name}")
            except Exception as e:
                logger.warning(f"RAG query failed for #{channel_name}: {e}")

        return result

    def _load_unrestricted(self) -> str:
        """Full access: MEMORY.md index + user profile."""
        parts = ["## Memory: Full Access Mode\n"]

        if self.memory_index.exists():
            index_content = self.memory_index.read_text(encoding="utf-8")
            parts.append("### Memory Registry\n")
            parts.append(index_content.strip())

        profile_path = self.memory_dir / "user_profile.md"
        if profile_path.exists():
            profile_body = self._strip_frontmatter(profile_path.read_text(encoding="utf-8"))
            parts.append("\n### User Profile\n")
            parts.append(profile_body.strip())

        parts.append(
            f"\n\n_All project/feedback/reference files available at {self.memory_dir} "
            "— use Read tool when needed._"
        )
        return "\n".join(parts)

    def _load_project_scoped(self, channel_name: str) -> str:
        """Project scope: Summary section + referenced project summaries."""
        file_stem = self._channel_map[channel_name]
        project_path = self.memory_dir / f"{file_stem}.md"

        if not project_path.exists():
            logger.warning(f"Project memory file not found: {project_path}")
            return ""

        content = project_path.read_text(encoding="utf-8")
        frontmatter = self._parse_frontmatter(content)
        project_name = frontmatter.get("name", channel_name)
        summary = self._extract_section(content, "Summary")
        references = frontmatter.get("references", []) or []

        self._touch_last_accessed(project_path, content)

        parts = [f"## Memory: Project Scope — #{channel_name}\n"]
        parts.append(f"### Project: {project_name}")

        if summary:
            parts.append(self._cap_lines(summary.strip(), SUMMARY_LINE_CAP))
        else:
            body = self._strip_frontmatter(content)
            parts.append(self._cap_lines(body.strip(), SUMMARY_LINE_CAP))

        parts.append(
            f"\n_Full context at: {project_path} — say \"load full context\" or ask "
            "about any aspect to trigger loading._"
        )

        if references:
            parts.append("\n### Referenced Context")
            for ref_stem in references:
                ref_path = self.memory_dir / f"{ref_stem}.md"
                if not ref_path.exists():
                    logger.warning(f"Referenced memory file not found: {ref_path}")
                    continue
                ref_content = ref_path.read_text(encoding="utf-8")
                ref_meta = self._parse_frontmatter(ref_content)

                if str(ref_meta.get("stale", "")).lower() == "true":
                    logger.debug(f"Skipping stale reference: {ref_stem}")
                    continue

                temp = self._get_temperature(ref_meta.get("last_accessed", ""))
                cap = REF_CAPS[temp]

                ref_name = ref_meta.get("name", ref_stem)
                ref_summary = self._extract_section(ref_content, "Summary")

                parts.append(f"\n#### {ref_name} _(ref: {temp})_")
                if ref_summary:
                    parts.append(self._cap_lines(ref_summary.strip(), cap))
                else:
                    parts.append(self._cap_lines(self._strip_frontmatter(ref_content).strip(), cap))

        return "\n".join(parts)

    def _get_temperature(self, last_accessed_str: str) -> str:
        """Return hot/warm/cold based on last_accessed date."""
        if not last_accessed_str:
            return "hot"
        try:
            last = date.fromisoformat(last_accessed_str.strip())
            days = (date.today() - last).days
            if days <= 7:
                return "hot"
            if days <= 30:
                return "warm"
            return "cold"
        except ValueError:
            return "hot"

    def _touch_last_accessed(self, path: Path, content: str) -> None:
        """Write today's date into last_accessed frontmatter field."""
        today = date.today().isoformat()
        try:
            if not content.startswith("---"):
                return
            parts = content.split("---", 2)
            if len(parts) < 3:
                return
            fm_lines = parts[1].splitlines(keepends=True)

            updated = False
            for i, line in enumerate(fm_lines):
                if line.startswith("last_accessed:"):
                    if line.strip() == f"last_accessed: {today}":
                        return
                    fm_lines[i] = f"last_accessed: {today}\n"
                    updated = True
                    break

            if not updated:
                fm_lines.append(f"last_accessed: {today}\n")

            new_content = "---" + "".join(fm_lines) + "---" + parts[2]
            path.write_text(new_content, encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to touch last_accessed on {path.name}: {e}")

    def _cap_lines(self, text: str, max_lines: int) -> str:
        """Truncate text to max_lines, appending ellipsis note if truncated."""
        lines = text.splitlines()
        if len(lines) <= max_lines:
            return text
        trimmed = len(lines) - max_lines
        return "\n".join(lines[:max_lines]) + f"\n_(+{trimmed} more lines — load full context to see all)_"

    def _parse_frontmatter(self, content: str) -> dict:
        """Extract YAML frontmatter as a simple dict. No PyYAML dependency."""
        if not content.startswith("---"):
            return {}
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}
        meta: dict = {}
        raw_lines = parts[1].splitlines()
        i = 0
        while i < len(raw_lines):
            line = raw_lines[i]
            if ":" in line and not line.startswith(" "):
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip()
                if val == "" or val == "[]":
                    items = []
                    i += 1
                    while i < len(raw_lines):
                        sub = raw_lines[i]
                        if sub.startswith("  - ") or sub.startswith("- "):
                            items.append(sub.strip().lstrip("- ").strip())
                            i += 1
                        else:
                            break
                    meta[key] = items
                    continue
                elif val.startswith("[") and val.endswith("]"):
                    items = [
                        x.strip().strip('"').strip("'")
                        for x in val[1:-1].split(",") if x.strip()
                    ]
                    meta[key] = items
                else:
                    meta[key] = val
            i += 1
        return meta

    def _extract_section(self, content: str, section_name: str) -> str:
        """Extract a ## Section block from markdown content."""
        pattern = rf"^## {re.escape(section_name)}\s*$"
        lines = content.splitlines()
        start = None
        for i, line in enumerate(lines):
            if re.match(pattern, line):
                start = i + 1
                break
        if start is None:
            return ""
        result = []
        for line in lines[start:]:
            if line.startswith("## "):
                break
            result.append(line)
        return "\n".join(result).strip()

    def _strip_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter block from content."""
        if not content.startswith("---"):
            return content
        parts = content.split("---", 2)
        return parts[2].strip() if len(parts) >= 3 else content
