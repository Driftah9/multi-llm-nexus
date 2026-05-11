"""
Space Registry — operator-agnostic organization layer.

A "Space" is whatever the operator needs it to be: a project, a business unit,
a client account, a research initiative, a personal category. The registry
maps short keys to metadata and enforces uniqueness.

Storage: spaces.yml in the config directory.

Structure:
  spaces:
    MKT:
      name: Digital Marketing
      domain: client
      created: 2026-05-10
      agent: marketing
      channels: [digital-marketing]
    INFRA:
      name: Infrastructure
      domain: ops
      created: 2026-05-10
      channels: [servers, networking]
"""

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


REGISTRY_FILE = "spaces.yml"


@dataclass
class SpaceEntry:
    key: str
    name: str
    domain: str = ""
    created: str = ""
    agent: str = ""
    channels: list[str] = field(default_factory=list)

    def tag_prefix(self, use_domain: bool = False) -> str:
        if use_domain and self.domain:
            return f"{self.domain}/{self.key}"
        return self.key

    def matches_channel(self, channel: str) -> bool:
        return channel in self.channels or channel == self.key.lower()


class SpaceRegistry:
    """Manages space key → metadata mappings."""

    def __init__(self, config_dir: Path):
        self.config_dir = Path(config_dir)
        self.registry_file = self.config_dir / REGISTRY_FILE
        self.spaces: dict[str, SpaceEntry] = {}
        self._load()

    def _load(self) -> None:
        if not self.registry_file.exists():
            self.spaces = {}
            return
        content = self.registry_file.read_text(encoding="utf-8")
        self.spaces = self._parse_yml(content)

    def _parse_yml(self, content: str) -> dict[str, SpaceEntry]:
        spaces = {}
        current_key = None
        current_data: dict = {}

        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())

            if indent == 2 and stripped.endswith(":"):
                if current_key and current_data:
                    spaces[current_key] = self._make_entry(current_key, current_data)
                current_key = stripped.rstrip(":")
                current_data = {}
            elif indent == 4 and current_key and ":" in stripped:
                key, val = stripped.split(":", 1)
                val = val.strip().strip('"').strip("'")
                if val.startswith("[") and val.endswith("]"):
                    val = [v.strip() for v in val[1:-1].split(",") if v.strip()]
                current_data[key.strip()] = val

        if current_key and current_data:
            spaces[current_key] = self._make_entry(current_key, current_data)
        return spaces

    def _make_entry(self, key: str, data: dict) -> SpaceEntry:
        channels = data.get("channels", [])
        if isinstance(channels, str):
            channels = [channels]
        return SpaceEntry(
            key=key,
            name=data.get("name", key),
            domain=data.get("domain", ""),
            created=data.get("created", ""),
            agent=data.get("agent", ""),
            channels=channels,
        )

    def _save(self) -> None:
        lines = [
            "# Nexus Space Registry",
            "# Managed by Nexus — edit via commands or wizard",
            "",
            "spaces:",
        ]
        for key, space in sorted(self.spaces.items()):
            lines.append(f"  {key}:")
            lines.append(f'    name: "{space.name}"')
            if space.domain:
                lines.append(f"    domain: {space.domain}")
            if space.created:
                lines.append(f"    created: {space.created}")
            if space.agent:
                lines.append(f"    agent: {space.agent}")
            if space.channels:
                ch_str = ", ".join(space.channels)
                lines.append(f"    channels: [{ch_str}]")
        self.registry_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def register(
        self,
        key: str,
        name: str,
        domain: str = "",
        agent: str = "",
        channels: list[str] = None,
    ) -> SpaceEntry:
        key = key.upper()
        if not re.match(r"^[A-Z0-9]{2,8}$", key):
            raise ValueError(f"Space key must be 2-8 uppercase letters/digits: {key!r}")
        if key in self.spaces:
            raise ValueError(f"Space key {key!r} already registered")

        entry = SpaceEntry(
            key=key,
            name=name,
            domain=domain,
            created=time.strftime("%Y-%m-%d"),
            agent=agent,
            channels=channels or [],
        )
        self.spaces[key] = entry
        self._save()
        return entry

    def update(self, key: str, **kwargs) -> SpaceEntry:
        key = key.upper()
        entry = self.spaces.get(key)
        if not entry:
            raise ValueError(f"Space {key!r} not found")
        for field_name in ("name", "domain", "agent", "channels"):
            if field_name in kwargs:
                setattr(entry, field_name, kwargs[field_name])
        self._save()
        return entry

    def remove(self, key: str) -> bool:
        key = key.upper()
        if key not in self.spaces:
            return False
        del self.spaces[key]
        self._save()
        return True

    def get(self, key: str) -> Optional[SpaceEntry]:
        return self.spaces.get(key.upper())

    def get_by_channel(self, channel: str) -> Optional[SpaceEntry]:
        for space in self.spaces.values():
            if space.matches_channel(channel):
                return space
        return None

    def get_by_domain(self, domain: str) -> list[SpaceEntry]:
        return [s for s in self.spaces.values() if s.domain == domain]

    def all_domains(self) -> list[str]:
        return sorted(set(s.domain for s in self.spaces.values() if s.domain))

    def all_keys(self) -> list[str]:
        return sorted(self.spaces.keys())

    def is_valid_key(self, key: str) -> bool:
        return key.upper() in self.spaces

    def parse_tag(self, tag: str) -> tuple[str, str, str]:
        """
        Parse a namespaced tag into (domain, space_key, topic).

        "INFRA/backups"          → ("", "INFRA", "backups")
        "ops/INFRA/backups"      → ("ops", "INFRA", "backups")
        "backups"                → ("", "", "backups")
        """
        parts = tag.split("/")
        if len(parts) == 3:
            return parts[0], parts[1], parts[2]
        elif len(parts) == 2:
            if parts[0].upper() in self.spaces:
                return "", parts[0].upper(), parts[1]
            return parts[0], "", parts[1]
        return "", "", tag

    def summary(self) -> str:
        if not self.spaces:
            return "No spaces registered yet."

        domains = self.all_domains()
        lines = []

        if domains:
            for domain in domains:
                spaces = self.get_by_domain(domain)
                lines.append(f"\n  {domain}/")
                for s in sorted(spaces, key=lambda x: x.key):
                    lines.append(f"    {s.key:<8} — {s.name}")
            undomained = [s for s in self.spaces.values() if not s.domain]
            if undomained:
                lines.append("\n  (uncategorized)")
                for s in sorted(undomained, key=lambda x: x.key):
                    lines.append(f"    {s.key:<8} — {s.name}")
        else:
            for s in sorted(self.spaces.values(), key=lambda x: x.key):
                lines.append(f"  {s.key:<8} — {s.name}")

        return "\n".join(lines)
