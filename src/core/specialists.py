"""
Specialist profile loader.

Reads role profiles from config/specialists/*.md, parsing YAML frontmatter
and markdown body into SpecialistProfile objects. Any LLM can execute these
profiles — they are provider-agnostic role definitions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("nexus.specialists")


@dataclass
class SpecialistProfile:
    id: str
    name: str
    tier: str = "standard"
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)
    data_sources: list[str] = field(default_factory=list)
    scope: str = ""

    @classmethod
    def from_dynamic(cls, name: str, focus: str, tier: str = "standard") -> "SpecialistProfile":
        spec_id = f"dynamic_{name.lower().replace(' ', '_')}"
        return cls(
            id=spec_id,
            name=name,
            tier=tier,
            system_prompt=f"You are the {name}. Your specific area of focus: {focus}.",
            scope=focus,
        )


class SpecialistLoader:
    """
    Loads specialist profiles from markdown files with YAML frontmatter.

    File format:
        ---
        id: financial
        name: Financial Compliance Specialist
        tier: standard
        tools: [invoice_api, ledger_read]
        ---
        You are the Financial Compliance Specialist...
        (this becomes the system_prompt)

    Files starting with _ are ignored (templates/docs).
    Files ending with .example are ignored (copy to .md to activate).
    """

    def __init__(self, specialists_dir: str = "config/specialists"):
        self._dir = Path(specialists_dir)
        self._profiles: dict[str, SpecialistProfile] = {}
        self._load_all()

    def get(self, specialist_id: str) -> Optional[SpecialistProfile]:
        return self._profiles.get(specialist_id)

    def list_ids(self) -> list[str]:
        return list(self._profiles.keys())

    def list_profiles(self) -> list[SpecialistProfile]:
        return list(self._profiles.values())

    def reload(self) -> int:
        self._profiles.clear()
        self._load_all()
        return len(self._profiles)

    def _load_all(self) -> None:
        if not self._dir.exists():
            logger.info(f"Specialists directory not found: {self._dir}")
            return

        for path in sorted(self._dir.glob("*.md")):
            if path.stem.startswith("_"):
                continue
            profile = self._parse_file(path)
            if profile:
                self._profiles[profile.id] = profile
                logger.debug(f"Loaded specialist: {profile.id} ({profile.name})")

        logger.info(f"Loaded {len(self._profiles)} specialist profile(s)")

    def _parse_file(self, path: Path) -> Optional[SpecialistProfile]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning(f"Could not read {path}: {e}")
            return None

        if not text.startswith("---"):
            logger.warning(f"No frontmatter in {path.name} — skipping")
            return None

        parts = text.split("---", 2)
        if len(parts) < 3:
            logger.warning(f"Malformed frontmatter in {path.name} — skipping")
            return None

        frontmatter = self._parse_yaml_simple(parts[1])
        if not frontmatter:
            logger.warning(f"Could not parse frontmatter in {path.name}")
            return None

        body = parts[2].strip()
        if not body:
            logger.warning(f"Empty body in {path.name} — skipping")
            return None

        spec_id = frontmatter.get("id", path.stem)
        return SpecialistProfile(
            id=spec_id,
            name=frontmatter.get("name", spec_id.replace("-", " ").title()),
            tier=frontmatter.get("tier", "standard"),
            system_prompt=body,
            tools=frontmatter.get("tools", []),
            data_sources=frontmatter.get("data_sources", []),
            scope=frontmatter.get("scope", ""),
        )

    @staticmethod
    def _parse_yaml_simple(text: str) -> Optional[dict]:
        """Minimal YAML frontmatter parser — no PyYAML dependency required.

        Handles:
          key: value
          key: [item1, item2]
          key: "quoted value"
        """
        result = {}
        for line in text.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue

            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()

            if not value:
                continue

            for q in ('"', "'"):
                if value.startswith(q) and value.endswith(q):
                    value = value[1:-1]
                    break

            if value.startswith("[") and value.endswith("]"):
                items = value[1:-1].split(",")
                result[key] = [item.strip().strip("'\"") for item in items if item.strip()]
            else:
                result[key] = value

        return result if result else None
