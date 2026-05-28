"""
Orchestrator — workspace-aware specialist dispatcher.

When a message arrives from a context mapped to an orchestrator-enabled
workspace, this module:
  1. Matches the message against workspace routing rules
  2. Spawns the relevant specialist tasks in parallel
  3. Synthesizes their outputs into a unified response

Provider-agnostic. Any LLM can run specialist profiles.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from .heartbeat import HeartbeatManager
from .specialists import SpecialistLoader, SpecialistProfile
from .session_state import SessionState, extract_claims

if TYPE_CHECKING:
    from .bridge import NexusBridge, BridgeResult

logger = logging.getLogger("nexus.orchestrator")


@dataclass
class SpecialistOutput:
    specialist_id: str
    specialist_name: str
    response: str
    cost_usd: float = 0.0
    elapsed: float = 0.0


@dataclass
class OrchestratorResult:
    response: str = ""
    specialists_used: list[str] = field(default_factory=list)
    synthesized: bool = False
    bypassed: bool = False
    total_cost: float = 0.0
    elapsed: float = 0.0
    session_state: Optional[SessionState] = None


@dataclass
class WorkspaceConfig:
    name: str
    display_name: str
    orchestrator: bool
    contexts: list[str]
    specialists: list[str]
    routing_rules: list[dict]
    default_specialists: list[str]
    specialist_tier: str = "standard"
    synthesis_tier: str = "standard"
    routing_mode: str = "llm"  # "llm" (default) or "keyword"


class Orchestrator:
    """
    Workspace-aware specialist dispatcher.

    The orchestrator sits between the engine and the bridge. When the engine
    detects that a message's context belongs to an orchestrator-enabled
    workspace, it delegates to the orchestrator instead of invoking the
    bridge directly.

    The orchestrator:
      - Resolves which specialists are relevant (keyword routing)
      - Invokes them in parallel via the bridge (one-shot, no history)
      - Synthesizes multiple specialist outputs into a single response
      - Returns the result to the engine for delivery
    """

    def __init__(
        self,
        bridge: NexusBridge,
        specialist_loader: SpecialistLoader,
        workspaces_config: dict,
    ):
        self.bridge = bridge
        self.specialists = specialist_loader
        self.workspaces: dict[str, WorkspaceConfig] = {}
        self._context_map: dict[str, str] = {}
        self._load_workspaces(workspaces_config)

    def _load_workspaces(self, config: dict) -> None:
        raw = config.get("workspaces", {})
        for ws_name, ws_data in raw.items():
            if not isinstance(ws_data, dict):
                continue

            ws = WorkspaceConfig(
                name=ws_name,
                display_name=ws_data.get("display_name", ws_name),
                orchestrator=ws_data.get("orchestrator", False),
                contexts=ws_data.get("contexts", []),
                specialists=ws_data.get("specialists", []),
                routing_rules=ws_data.get("routing_rules", []),
                default_specialists=ws_data.get("default_specialists", []),
                specialist_tier=ws_data.get("specialist_tier", "standard"),
                synthesis_tier=ws_data.get("synthesis_tier", "standard"),
                routing_mode=ws_data.get("routing_mode", "llm"),
            )
            self.workspaces[ws_name] = ws

            for ctx in ws.contexts:
                self._context_map[ctx] = ws_name

        logger.info(
            f"Loaded {len(self.workspaces)} workspace(s), "
            f"{len(self._context_map)} context mapping(s)"
        )

    # ── Public API ─────────────────────────────────────────────────

    def should_orchestrate(self, context: str) -> bool:
        ws_name = self._context_map.get(context)
        if not ws_name:
            return False
        return self.workspaces[ws_name].orchestrator

    def get_workspace(self, context: str) -> Optional[WorkspaceConfig]:
        ws_name = self._context_map.get(context)
        return self.workspaces.get(ws_name) if ws_name else None

    def get_workspace_for_display(self, context: str) -> Optional[str]:
        ws = self.get_workspace(context)
        return ws.display_name if ws else None

    async def dispatch(
        self,
        message: str,
        context: str,
        session_key: str,
        operator_context: str = "",
        heartbeat: Optional[HeartbeatManager] = None,
        live_context: Optional[str] = None,
        session_state: Optional[SessionState] = None,
    ) -> OrchestratorResult:
        """
        Route a message through specialist agents and return synthesized output.

        Args:
            message: The operator's message text.
            context: Adapter context identifier (channel name, topic, etc.).
            session_key: Session key for the main conversation.
            operator_context: Additional context to prepend (platform, workspace info).
            heartbeat: Optional HeartbeatManager for live status display updates.
            live_context: Optional recent conversation transcript fetched by the adapter.
                          Injected into each specialist's context for higher fidelity
                          than the rolling history window alone. Adapters populate this
                          from their native thread/channel history APIs.
            session_state: Optional prior SessionState for this session.
                           If provided, accumulated claims and locked decisions are
                           injected into each specialist's context. Updated on return.

        Returns:
            OrchestratorResult with the final response and updated session_state.
        """
        start = time.time()
        workspace = self.get_workspace(context)

        if not workspace or not workspace.orchestrator:
            return OrchestratorResult(bypassed=True)

        # Create or use existing session state for claim tracking
        state = session_state or SessionState(
            session_key=session_key,
            channel_name=context,
            workspace_name=workspace.name,
        )

        try:
            specialist_ids = await asyncio.wait_for(
                self._route_to_specialists(message, workspace), timeout=30.0
            )
        except asyncio.TimeoutError:
            logger.warning(f"Specialist routing timed out in {workspace.name} — bypassing")
            return OrchestratorResult(bypassed=True)

        if not specialist_ids:
            logger.debug(f"No routing match in {workspace.name} — bypassing orchestrator")
            return OrchestratorResult(bypassed=True)

        profiles = self._resolve_profiles(specialist_ids)
        if not profiles:
            logger.warning(f"No loadable profiles for {specialist_ids} — bypassing")
            return OrchestratorResult(bypassed=True)

        logger.info(
            f"Orchestrating in {workspace.name}: "
            f"specialists={[p.id for p in profiles]}"
        )

        if heartbeat:
            await heartbeat.set_provider(
                heartbeat.state.display_prefix, "Orchestrator", None
            )
            await heartbeat.set_agents([p.name for p in profiles])

        outputs = await self._invoke_specialists(
            profiles, message, session_key, workspace, operator_context,
            heartbeat, live_context, state
        )

        if not outputs:
            logger.warning("All specialists failed — bypassing orchestrator")
            return OrchestratorResult(bypassed=True)

        # Extract claims from specialist outputs and run conflict detection
        for output in outputs:
            claims = extract_claims(output.specialist_id, output.response)
            if claims:
                state.add_claims(output.specialist_id, claims)
        if len(outputs) > 1:
            state.detect_conflicts()

        if len(outputs) == 1:
            if heartbeat:
                await heartbeat.set_agents([])
            return OrchestratorResult(
                response=outputs[0].response,
                specialists_used=[outputs[0].specialist_id],
                synthesized=False,
                total_cost=outputs[0].cost_usd,
                elapsed=time.time() - start,
                session_state=state,
            )

        if heartbeat:
            await heartbeat.set_agents([])
            await heartbeat.set_phase("synthesizing")

        synthesized = await self._synthesize(
            message, outputs, session_key, workspace, operator_context, state
        )

        total_cost = sum(o.cost_usd for o in outputs) + synthesized.cost_usd
        return OrchestratorResult(
            response=synthesized.text,
            specialists_used=[o.specialist_id for o in outputs],
            synthesized=True,
            total_cost=total_cost,
            elapsed=time.time() - start,
            session_state=state,
        )

    # ── Routing ────────────────────────────────────────────────────

    async def _route_to_specialists(
        self, message: str, workspace: WorkspaceConfig
    ) -> list[str]:
        """Route message to specialists. LLM-based by default, keyword fallback."""
        if workspace.routing_mode == "keyword":
            return self._route_to_specialists_keyword(message, workspace)
        return await self._route_to_specialists_llm(message, workspace)

    async def _route_to_specialists_llm(
        self, message: str, workspace: WorkspaceConfig
    ) -> list:  # list[str | dict] — str=known ID, dict=dynamic spec {name, focus}
        """Ask the configured LLM which specialists should handle this message.

        The LLM may select from known specialist IDs or request a dynamic specialist
        using the syntax: new:Name:focus_description
        """
        if not workspace.specialists:
            return []

        specialist_descs = []
        for sid in workspace.specialists:
            profile = self.specialists.get(sid)
            if profile:
                specialist_descs.append(f"- {sid}: {profile.name}")
            else:
                specialist_descs.append(f"- {sid}")

        routing_prompt = (
            f"Which specialists should handle this message?\n\n"
            f"Message: {message}\n\n"
            f"Available specialists:\n" + "\n".join(specialist_descs) + "\n\n"
            f"Reply with relevant specialist IDs, comma-separated.\n"
            f"If the message needs expertise not covered by the listed specialists, "
            f"add a dynamic specialist using: new:Name:focus_description\n"
            f"Example: financial, new:Tax_Advisor:tax_deductions_and_liability\n"
            f"Reply 'none' if no specialist is needed. Reply 'all' to use all."
        )

        try:
            result = await self.bridge.invoke(
                prompt=routing_prompt,
                session_key="__nexus_routing__",
                tier="nano",
                ephemeral=True,
            )
            response = result.text.strip().lower()

            if response == "none":
                return []
            if response == "all":
                return list(workspace.specialists)

            available = set(workspace.specialists)
            selected = []
            for token in response.replace(",", " ").split():
                token = token.strip()
                if not token:
                    continue
                if token.startswith("new:"):
                    parts = token.split(":", 2)
                    if len(parts) == 3 and parts[1] and parts[2]:
                        selected.append({
                            "name": parts[1].replace("_", " ").title(),
                            "focus": parts[2].replace("_", " "),
                        })
                elif token in available:
                    selected.append(token)

            if selected:
                return selected

            logger.debug(
                f"LLM routing returned unrecognized tokens, falling back to keyword"
            )
        except Exception as e:
            logger.warning(f"LLM routing failed ({e}), falling back to keyword")

        return self._route_to_specialists_keyword(message, workspace)

    def _route_to_specialists_keyword(
        self, message: str, workspace: WorkspaceConfig
    ) -> list[str]:
        """Keyword substring routing — fallback when LLM routing is unavailable."""
        msg_lower = message.lower()
        matched: set[str] = set()

        for rule in workspace.routing_rules:
            keywords = rule.get("keywords", [])
            if any(kw in msg_lower for kw in keywords):
                matched.update(rule.get("specialists", []))

        if not matched and workspace.default_specialists:
            matched.update(workspace.default_specialists)

        available = set(workspace.specialists)
        return [s for s in matched if s in available]

    def _resolve_profiles(self, specialist_ids: list) -> list[SpecialistProfile]:
        """Resolve a mix of known IDs (str) and dynamic specs (dict) into profiles."""
        profiles = []
        for item in specialist_ids:
            if isinstance(item, dict):
                profile = SpecialistProfile.from_dynamic(
                    name=item["name"],
                    focus=item["focus"],
                )
                logger.info(f"Created dynamic specialist: {profile.id} ({profile.name})")
                profiles.append(profile)
            else:
                profile = self.specialists.get(item)
                if profile:
                    profiles.append(profile)
                else:
                    logger.warning(f"Specialist profile not found: {item}")
        return profiles

    # ── Specialist Invocation ──────────────────────────────────────

    async def _invoke_specialists(
        self,
        profiles: list[SpecialistProfile],
        message: str,
        session_key: str,
        workspace: WorkspaceConfig,
        operator_context: str,
        heartbeat: Optional[HeartbeatManager] = None,
        live_context: Optional[str] = None,
        session_state: Optional[SessionState] = None,
    ) -> list[SpecialistOutput]:
        active_names = [p.name for p in profiles]

        async def _tracked(profile: SpecialistProfile) -> SpecialistOutput:
            peers = [p for p in profiles if p.id != profile.id]
            try:
                return await self._invoke_one(
                    profile, message, session_key, workspace, operator_context,
                    peers, live_context, session_state
                )
            finally:
                try:
                    active_names.remove(profile.name)
                except ValueError:
                    pass
                if heartbeat:
                    await heartbeat.set_agents(list(active_names))

        tasks = [_tracked(profile) for profile in profiles]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        outputs = []
        for profile, result in zip(profiles, results):
            if isinstance(result, Exception):
                logger.error(f"Specialist {profile.id} failed: {result}")
                continue
            outputs.append(result)

        return outputs

    async def _invoke_one(
        self,
        profile: SpecialistProfile,
        message: str,
        session_key: str,
        workspace: WorkspaceConfig,
        operator_context: str,
        peers: Optional[list[SpecialistProfile]] = None,
        live_context: Optional[str] = None,
        session_state: Optional[SessionState] = None,
    ) -> SpecialistOutput:
        specialist_key = f"{session_key}__specialist__{profile.id}"
        start = time.time()

        system = self._build_specialist_system(
            profile, workspace, operator_context, peers, live_context, session_state
        )

        result: BridgeResult = await self.bridge.invoke(
            prompt=message,
            session_key=specialist_key,
            tier=profile.tier or workspace.specialist_tier,
            system_prompt=system,
            ephemeral=True,
        )

        return SpecialistOutput(
            specialist_id=profile.id,
            specialist_name=profile.name,
            response=result.text,
            cost_usd=result.cost_usd,
            elapsed=time.time() - start,
        )

    def _build_specialist_system(
        self,
        profile: SpecialistProfile,
        workspace: WorkspaceConfig,
        operator_context: str,
        peers: Optional[list[SpecialistProfile]] = None,
        live_context: Optional[str] = None,
        session_state: Optional[SessionState] = None,
    ) -> str:
        parts = [profile.system_prompt]

        if operator_context:
            parts.append(f"\n\n## Context\n{operator_context}")

        if live_context:
            parts.append(f"\n\n## Recent Conversation\n{live_context}")

        if session_state:
            prior = session_state.render_for_specialists()
            if prior:
                parts.append(f"\n\n{prior}")

        if peers:
            peer_lines = []
            for peer in peers:
                coverage = peer.scope or peer.name
                peer_lines.append(f"- {peer.name}: {coverage}")
            parts.append(
                f"\n\n## Peer Specialists (do not duplicate their analysis)\n"
                + "\n".join(peer_lines)
            )

        parts.append(
            f"\n\nYou are responding as the {profile.name} within the "
            f"{workspace.display_name} workspace. "
            + ("Focus ONLY on YOUR area of expertise — peers listed above handle the rest. "
               if peers else
               "Other specialists may also be analyzing this same question from their domain. "
               "Focus on YOUR area of expertise. ")
            + "Be concise and actionable."
        )

        return "\n".join(parts)

    # ── Synthesis ──────────────────────────────────────────────────

    async def _synthesize(
        self,
        original_message: str,
        outputs: list[SpecialistOutput],
        session_key: str,
        workspace: WorkspaceConfig,
        operator_context: str,
        session_state: Optional[SessionState] = None,
    ) -> BridgeResult:
        synthesis_prompt = self._build_synthesis_prompt(original_message, outputs, session_state)
        synthesis_system = (
            "You are the Chief of Staff synthesizing specialist analyses into "
            "a unified briefing for the Operator. Present a clear, actionable "
            "response that integrates all specialist perspectives. Where "
            "specialists agree, state the consensus. Where they conflict, "
            "present BOTH views with the tradeoff — never silently resolve conflicts. "
            "Lead with the recommendation, then supporting analysis. Be concise."
        )

        if operator_context:
            synthesis_system += f"\n\n## Context\n{operator_context}"

        return await self.bridge.invoke(
            prompt=synthesis_prompt,
            session_key=f"{session_key}__synthesis",
            tier=workspace.synthesis_tier,
            system_prompt=synthesis_system,
            ephemeral=True,
        )

    @staticmethod
    def _build_synthesis_prompt(
        original_message: str,
        outputs: list[SpecialistOutput],
        session_state: Optional[SessionState] = None,
    ) -> str:
        sections = [f"## Operator's Question\n\n{original_message}\n"]

        for output in outputs:
            sections.append(
                f"## {output.specialist_name} Analysis\n\n{output.response}\n"
            )

        if session_state:
            conflict_block = session_state.render_for_synthesis()
            if conflict_block:
                sections.append(conflict_block)

        sections.append(
            "## Your Task\n\n"
            "Synthesize the specialist analyses above into a single, unified "
            "briefing for the Operator. Integrate insights, surface any detected "
            "conflicts above, and provide a clear recommendation."
        )

        return "\n".join(sections)

    # ── Admin ──────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "workspaces": {
                name: {
                    "display_name": ws.display_name,
                    "orchestrator": ws.orchestrator,
                    "contexts": ws.contexts,
                    "specialists": ws.specialists,
                }
                for name, ws in self.workspaces.items()
            },
            "context_map": dict(self._context_map),
            "loaded_specialists": self.specialists.list_ids(),
        }
