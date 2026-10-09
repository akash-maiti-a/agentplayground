"""LLM-agent seam tests (offline deterministic backend)."""

from __future__ import annotations

from arena.agents import DeterministicBackend, LLMAgent, RuleChallenger, RuleWarden
from arena.agents.llm import _extract_json
from arena.match import run_match
from arena.schema import EventType, Side, validate_intent


def test_extract_json_is_robust():
    assert _extract_json('prefix {"verb": "HOLD"} suffix') == {"verb": "HOLD"}
    assert _extract_json("no json here") is None
    assert _extract_json('{"a": {"b": 1}} trailing') == {"a": {"b": 1}}


def test_llm_outputs_are_always_schema_valid_or_noop():
    """Even with deliberately garbled replies, nothing invalid reaches the engine."""
    agent = LLMAgent(Side.WARDEN, DeterministicBackend(garble_every=3))
    res = run_match("T1", 5, RuleChallenger(), agent)
    assert not res.log.events_of(EventType.INTENT_REJECTED.value)
    assert agent.parse_failures > 0  # the garbled replies were caught by the fallback


def test_llm_match_is_deterministic():
    def go():
        return run_match("T1", 4471, LLMAgent(Side.CHALLENGER, DeterministicBackend()),
                         RuleWarden()).final_hash
    assert go() == go()


def test_llm_challenger_makes_progress():
    from arena.scoring import score_match
    agent = LLMAgent(Side.CHALLENGER, DeterministicBackend())
    res = run_match("T1", 4471, agent, RuleWarden())
    card = score_match(res.log)
    assert card.challenger["objective_progress"] >= 0.2


def test_every_backend_reply_parses_to_valid_or_rejected():
    backend = DeterministicBackend()
    agent = LLMAgent(Side.WARDEN, backend)
    # feed a minimal observation; the parsed intent must validate
    obs = {"tick": 1, "availability": 1.0, "sla_floor": 0.8, "inventory": {},
           "feed": [{"tick": 1, "node": "gate-00", "zone": "perimeter",
                     "strength": 0.5, "category": "auth-anomaly"}],
           "findings": [], "zones": ["perimeter"]}
    intent = agent.observe(obs, ap_remaining=6)
    assert validate_intent(intent).ok
