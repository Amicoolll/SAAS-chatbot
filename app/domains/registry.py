"""Domain plugin registry.

Maps an ``agent_type`` value to the plugin instance that handles its tools.
Static lookup for now — we don't need hot reloading or DI.

When new domains land (HR, law, etc.) just add them here; no other code
in this package needs to change.
"""

from __future__ import annotations

from app.domains.aviation.plugin import AviationDomain
from app.domains.base import DomainPlugin


# Module-level singletons. Plugin instances are stateless apart from their
# (lazily-built) HTTP client; a single instance per process is fine.
_AVIATION = AviationDomain()


# agent_type → plugin
_REGISTRY: dict[str, DomainPlugin] = {
    key: _AVIATION for key in AviationDomain.agent_keys
}


def get_domain_for_agent(agent_type: str) -> DomainPlugin | None:
    """Return the plugin handling this agent_type, or None if no plugin
    owns it (in which case the chat flow stays in pure-RAG mode).

    Looking up an unknown agent_type returns None, not an error — the
    caller decides whether absence is a problem.
    """
    return _REGISTRY.get(agent_type)


def all_domains() -> list[DomainPlugin]:
    """Distinct plugin instances. Useful for tests and admin endpoints."""
    seen: set[int] = set()
    out: list[DomainPlugin] = []
    for plugin in _REGISTRY.values():
        if id(plugin) not in seen:
            seen.add(id(plugin))
            out.append(plugin)
    return out
