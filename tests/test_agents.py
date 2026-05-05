"""Unit tests for agent prompt registry — specifically the aviation agent
which enforces a safety-critical domain guardrail.
"""

from __future__ import annotations

from app.agents.prompts import AgentConfig, get_agent, list_agents


def test_aviation_agent_exists_and_is_not_fallback():
    agent = get_agent("aviation")
    assert isinstance(agent, AgentConfig)
    assert agent.key == "aviation"
    # Must NOT be the generic fallback.
    assert agent.key != get_agent("__does_not_exist__").key


def test_aviation_agent_appears_in_list():
    items = list_agents()
    assert any(
        a["key"] == "aviation" and a["name"] == "Aviation Support Assistant"
        for a in items
    )


def test_aviation_prompt_contains_safety_guardrails():
    """Regression guard: the safety-critical guardrails are what make this
    agent fit for aviation use. Deleting them would silently remove the
    "never invent" behaviour — this test catches that.
    """
    agent = get_agent("aviation")
    system = agent.system_prompt.lower()
    assert "safety-critical" in system
    assert "never invent" in system
    assert "aviation" in system
    # Domain redirect guard for off-topic questions.
    assert "redirect" in system or "outside aviation" in system


def test_unknown_agent_falls_back_to_general_not_aviation():
    agent = get_agent("random_not_a_real_agent_key")
    assert agent.key == "general"
