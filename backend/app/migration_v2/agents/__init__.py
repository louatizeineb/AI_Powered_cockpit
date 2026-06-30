"""Versioned, allowlisted agent contracts for migration_v2 orchestration."""

from app.migration_v2.agents.manifests import AGENT_MANIFESTS, AgentManifest, get_agent_manifest

__all__ = ["AGENT_MANIFESTS", "AgentManifest", "get_agent_manifest"]
