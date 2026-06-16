"""Research cache manager — persistent markdown-based storage with TTL.

Cache layout:
  $NEXUS_CACHE_DIR/
    general/
      <topic>/
        summary.md          (synthesized result)
        metadata.json       (TTL, sources, timestamp)
        raw/
          <url-hash>.md     (raw page content per source)
    projects/
      <project_name>/
        <topic>/
          summary.md, metadata.json, raw/

TTL: general=7 days, project=30 days (configurable via metadata).

Cache root: $NEXUS_CACHE_DIR  (default: $NEXUS_DATA_DIR/research_cache)
"""

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_DATA_DIR = Path(os.environ.get("NEXUS_DATA_DIR", Path(__file__).parent.parent.parent / "data"))
CACHE_ROOT = Path(os.environ.get("NEXUS_CACHE_DIR", _DATA_DIR / "research_cache"))
GENERAL_CACHE = CACHE_ROOT / "general"
PROJECTS_CACHE = CACHE_ROOT / "projects"

GENERAL_TTL_DAYS = 7
PROJECT_TTL_DAYS = 30


def _sanitize_topic(topic: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in topic.lower())[:80]


def _url_hash(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


class ResearchCache:
    def __init__(self, scope: str = "general", project_name: str | None = None):
        """Initialize cache for a scope.

        Args:
            scope: "general" or "project"
            project_name: Required if scope="project"
        """
        self.scope = scope
        self.project_name = project_name

        if scope == "general":
            self.base_path = GENERAL_CACHE
            self.ttl_days = GENERAL_TTL_DAYS
        elif scope == "project" and project_name:
            self.base_path = PROJECTS_CACHE / project_name
            self.ttl_days = PROJECT_TTL_DAYS
        else:
            raise ValueError(f"Invalid scope: {scope!r} (project_name required for 'project')")

        self.base_path.mkdir(parents=True, exist_ok=True)

    def topic_path(self, topic: str) -> Path:
        return self.base_path / _sanitize_topic(topic)

    def get_summary(self, topic: str) -> str | None:
        """Retrieve cached summary if valid (not expired). Returns None on miss/expiry."""
        topic_path = self.topic_path(topic)
        summary_file = topic_path / "summary.md"
        metadata_file = topic_path / "metadata.json"

        if not summary_file.exists():
            return None

        if metadata_file.exists():
            try:
                meta = json.loads(metadata_file.read_text())
                timestamp = datetime.fromisoformat(meta.get("timestamp", ""))
                ttl = timedelta(days=meta.get("ttl_days", self.ttl_days))
                if datetime.now() - timestamp > ttl:
                    logger.info(f"Cache expired: {self.scope}/{topic}")
                    return None
            except (json.JSONDecodeError, ValueError):
                pass

        return summary_file.read_text()

    def save_summary(
        self,
        topic: str,
        summary: str,
        sources: list[str],
        raw_pages: dict[str, str] | None = None,
    ) -> None:
        """Save research result to cache."""
        topic_path = self.topic_path(topic)
        topic_path.mkdir(parents=True, exist_ok=True)

        (topic_path / "summary.md").write_text(summary)
        (topic_path / "metadata.json").write_text(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "ttl_days": self.ttl_days,
            "topic": topic,
            "sources": sources,
            "scope": self.scope,
            "project_name": self.project_name,
        }, indent=2))

        if raw_pages:
            raw_dir = topic_path / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            for url, content in raw_pages.items():
                (raw_dir / f"{_url_hash(url)}.md").write_text(f"# Source: {url}\n\n{content}")

        logger.info(f"Cached research: {self.scope}/{topic}")

    def get_raw_pages(self, topic: str) -> dict[str, str]:
        """Retrieve raw cached pages for a topic."""
        raw_dir = self.topic_path(topic) / "raw"
        if not raw_dir.exists():
            return {}
        pages = {}
        for page_file in raw_dir.glob("*.md"):
            try:
                content = page_file.read_text()
                lines = content.split("\n", 1)
                if lines[0].startswith("# Source: "):
                    url = lines[0].replace("# Source: ", "").strip()
                    pages[url] = "\n".join(lines[1:]) if len(lines) > 1 else ""
            except Exception as e:
                logger.warning(f"Error reading raw page {page_file}: {e}")
        return pages

    def invalidate(self, topic: str | None = None) -> None:
        """Invalidate a specific topic or the entire scope cache."""
        import shutil
        if topic:
            topic_path = self.topic_path(topic)
            if topic_path.exists():
                shutil.rmtree(topic_path)
                logger.info(f"Invalidated cache: {self.scope}/{topic}")
        else:
            if self.base_path.exists():
                shutil.rmtree(self.base_path)
                self.base_path.mkdir(parents=True, exist_ok=True)
                logger.info(f"Invalidated entire {self.scope} cache")


def get_cache(scope: str = "general", project_name: str | None = None) -> ResearchCache:
    """Factory for cache instances."""
    return ResearchCache(scope=scope, project_name=project_name)
