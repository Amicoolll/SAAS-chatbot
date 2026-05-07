"""Multi-domain chatbot plugin interface.

Each domain (aviation, hr, law, ...) ships a subclass of :class:`DomainPlugin`
that declares its agent identity, the tools (functions) the LLM can call,
and how to execute those tools. Shared chat orchestration in
``app.api.chat_pg`` will eventually look up the active domain and route
intent → tool through this surface.

This is intentionally minimal for slice 1 (Retrieve booking). Surface
will grow as slices add: domain seed sentences for the strict-domain
centroid, intent classifiers, workflow registration, etc. Add fields as
real use cases require them — don't speculate.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass(frozen=True)
class ToolSpec:
    """Declarative description of a single tool a domain exposes.

    The ``parameters_schema`` is JSON Schema (Draft-07-style dict) suitable
    for handing directly to OpenAI function-calling. Keeping it as a plain
    dict (not a Pydantic class) avoids forcing every consumer to import
    Pydantic just to read the schema.
    """

    name: str
    description: str
    parameters_schema: dict[str, Any]


class DomainPlugin(ABC):
    """Base class for a domain (aviation, hr, law, ...).

    Subclasses set ``name`` and ``agent_keys`` as class variables, then
    implement :meth:`tools` and :meth:`dispatch_tool`.
    """

    #: Stable domain identifier, lowercase ascii. Used in routing and logs.
    name: ClassVar[str]

    #: Which ``agent_type`` values from ``app.agents.prompts`` this domain
    #: handles. Multiple keys allowed (e.g. aviation might own both
    #: "aviation" and "aviation_maintenance" if those diverge later).
    agent_keys: ClassVar[list[str]]

    @abstractmethod
    def tools(self) -> list[ToolSpec]:
        """Return the full list of tools this domain exposes to the LLM.

        Used to construct the OpenAI function-calling tool list at chat
        orchestration time.
        """

    @abstractmethod
    def dispatch_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute ``tool_name`` with the given arguments and return the
        JSON-serializable result.

        Implementations should:

        - Validate ``args`` against the matching :class:`ToolSpec`'s
          ``parameters_schema`` (or rely on a Pydantic model in the
          implementation, which is what aviation does).
        - Raise :class:`ValueError` for an unknown ``tool_name`` so the
          orchestrator can return a clean error.
        - Surface partner/API errors as their own exception types — do
          not swallow them.
        """

    def tool_names(self) -> set[str]:
        """Convenience: set of valid tool names for this domain."""
        return {t.name for t in self.tools()}
