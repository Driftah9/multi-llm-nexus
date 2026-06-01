#!/usr/bin/env python3
"""
RAG ingest — chunks memory / project-docs / obsidian into ChromaDB.

Ported from claude-brain with all paths configurable via CLI args or env vars.
Run manually or via cron. Safe to re-run (upsert, not append).

Usage:
    python scripts/rag_ingest.py                            # full ingest, default dirs
    python scripts/rag_ingest.py --memory-dir /custom/path  # override memory source
    python scripts/rag_ingest.py --namespace memory         # ingest only memory
    python scripts/rag_ingest.py --help                     # see all options
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Allow running from scripts/ dir or project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.rag_store import RagStore  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Directories to skip when walking projects/
SKIP_DIRS = {
    "node_modules", "venv", ".venv", "build", "__pycache__",
    ".git", "dist", ".next", "target", ".cache", "coverage",
}

# Chunk parameters
CHUNK_SIZE = 500   # characters
OVERLAP = 50


# ── Chunking ────────────────────────────────────────────────────────────────

def chunk_text(text: str) -> list[str]:
    """Split text into overlapping chunks of ~CHUNK_SIZE chars."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= CHUNK_SIZE:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + CHUNK_SIZE])
        start += CHUNK_SIZE - OVERLAP
    return chunks


# ── Ingest helpers ───────────────────────────────────────────────────────────

def _should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def ingest_files(store: RagStore, paths: list[Path], namespace: str) -> int:
    """Chunk and upsert a list of files into a namespace. Returns doc count."""
    docs = []
    for path in paths:
        if _should_skip(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
            if not text:
                continue
            for i, chunk in enumerate(chunk_text(text)):
                docs.append({
                    "id": f"{path}::{i}",
                    "text": chunk,
                    "metadata": {"source": str(path), "chunk": i},
                })
        except Exception as exc:
            logger.warning(f"Skipping {path}: {exc}")

    if not docs:
        return 0

    count = store.ingest(docs, namespace)
    logger.info(f"[{namespace}] {count}/{len(docs)} chunks from {len(paths)} file(s)")
    return count


# ── Namespace ingest functions ───────────────────────────────────────────────

def ingest_memory(store: RagStore, memory_dir: Path) -> int:
    if not memory_dir.exists():
        logger.warning(f"Memory dir not found: {memory_dir}")
        return 0
    paths = list(memory_dir.glob("*.md"))
    logger.info(f"[memory] Found {len(paths)} files in {memory_dir}")
    return ingest_files(store, paths, "memory")


def ingest_projects(store: RagStore, projects_dir: Path) -> int:
    if not projects_dir.exists():
        logger.warning(f"Projects dir not found: {projects_dir}")
        return 0

    paths: list[Path] = []
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir() or _should_skip(project_dir):
            continue
        # Only top-level markdown docs — never recurse into src/build
        for name in ("README.md", "AGENTS.md"):
            p = project_dir / name
            if p.exists():
                paths.append(p)
        docs_dir = project_dir / "docs"
        if docs_dir.is_dir():
            paths.extend(docs_dir.glob("*.md"))

    logger.info(f"[projects] Found {len(paths)} doc files across {projects_dir}")
    return ingest_files(store, paths, "projects")


def ingest_obsidian(store: RagStore, obsidian_dir: Path) -> int:
    if not obsidian_dir.exists():
        logger.warning(f"Obsidian vault not found: {obsidian_dir}")
        return 0
    paths = list(obsidian_dir.rglob("*.md"))
    logger.info(f"[obsidian] Found {len(paths)} notes in {obsidian_dir}")
    return ingest_files(store, paths, "obsidian")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="RAG ingest — chunk memory/projects/obsidian into ChromaDB"
    )
    parser.add_argument(
        "--memory-dir",
        type=Path,
        default=None,
        help="Memory files directory (from NEXUS_MEMORY_DIR env or ~/.claude/projects/-home-claude/memory)"
    )
    parser.add_argument(
        "--projects-dir",
        type=Path,
        default=None,
        help="Projects directory (from NEXUS_PROJECTS_DIR env or ~/projects)"
    )
    parser.add_argument(
        "--obsidian-dir",
        type=Path,
        default=None,
        help="Obsidian vault directory (from NEXUS_OBSIDIAN_DIR env or ~/obsidian-vault)"
    )
    parser.add_argument(
        "--namespace",
        choices=["memory", "projects", "obsidian"],
        default=None,
        help="Ingest only this namespace (default: all)",
    )
    args = parser.parse_args()

    # Resolve directories: CLI arg > env var > default
    memory_dir = (
        args.memory_dir or
        Path(os.environ.get("NEXUS_MEMORY_DIR", "")) or
        Path.home() / ".claude/projects/-home-claude/memory"
    )
    projects_dir = (
        args.projects_dir or
        Path(os.environ.get("NEXUS_PROJECTS_DIR", "")) or
        Path.home() / "projects"
    )
    obsidian_dir = (
        args.obsidian_dir or
        Path(os.environ.get("NEXUS_OBSIDIAN_DIR", "")) or
        Path.home() / "obsidian-vault"
    )

    logger.info(f"Memory: {memory_dir}")
    logger.info(f"Projects: {projects_dir}")
    logger.info(f"Obsidian: {obsidian_dir}")

    store = RagStore()
    if not store.available:
        logger.error("RagStore not available — aborting")
        sys.exit(1)

    before = store.collection_counts()
    logger.info(f"Collections before: {before}")

    total = 0
    target = args.namespace

    if target in (None, "memory"):
        total += ingest_memory(store, memory_dir)
    if target in (None, "projects"):
        total += ingest_projects(store, projects_dir)
    if target in (None, "obsidian"):
        total += ingest_obsidian(store, obsidian_dir)

    after = store.collection_counts()
    logger.info(f"Collections after:  {after}")
    logger.info(f"Ingest complete — {total} chunks upserted total")


if __name__ == "__main__":
    main()
