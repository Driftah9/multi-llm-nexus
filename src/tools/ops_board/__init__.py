"""Nexus Ops Board — a local admin console for a Nexus deployment.

Agnostic and config-driven: every tab reads the operator's own Nexus config or a live
scan, never a baked-in roster. Launch with `nexus ops-board` (or `python -m
src.tools.ops_board`). Local only — nothing is transmitted anywhere.
"""
from .app import app, serve

__all__ = ["app", "serve"]
