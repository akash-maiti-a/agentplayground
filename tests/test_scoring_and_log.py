"""Scoring determinism and log-integrity (hash-chain) tests."""

from __future__ import annotations

from arena.agents import RuleChallenger, RuleWarden, ScriptedAgent
from arena.match import run_match
from arena.schema import Intent, Side
from arena.scoring import score_match


def test_scorecard_is_deterministic():
    r1 = run_match("T1", 4471, RuleChallenger(), RuleWarden())
    r2 = run_match("T1", 4471, RuleChallenger(), RuleWarden())
    assert score_match(r1.log).to_dict() == score_match(r2.log).to_dict()


def test_log_chain_valid_and_tamper_evident():
    res = run_match("T1", 3, RuleChallenger(), RuleWarden())
    assert res.log.verify_chain()
    # tamper with a middle record's event payload
    assert len(res.log.records) > 5
    res.log.records[3]["event"]["tick"] = 99999
    assert not res.log.verify_chain()


def test_scored_metrics_in_bounds():
    res = run_match("T2", 11, RuleChallenger(), RuleWarden())
    card = score_match(res.log)
    for v in card.warden.values():
        assert v >= -1.0
    for key in ("objective_progress", "stealth", "efficiency"):
        assert 0.0 <= card.challenger[key] <= 1.0
    assert 0.0 <= card.integrity["rule_compliance"] <= 1.0
    assert card.notes["chain_valid"] is True


def test_scripted_golden_transcript():
    """A fixed script against a passive opponent yields a stable foothold.

    This is a deterministic 'golden' check: the scripted challenger probes and
    accesses the public perimeter portal; against a do-nothing warden the match
    is fully reproducible.
    """
    # Discover + probe + repeatedly access the perimeter entry seen at t=0.
    from arena.scenario import generate_world
    w = generate_world("T0", 100)
    entry = sorted(n.id for n in w.nodes.values() if n.zone == "perimeter")[0]

    script = [Intent(Side.CHALLENGER, "PROBE", {"node": entry, "service": "portal"})]
    script += [Intent(Side.CHALLENGER, "ACCESS", {"node": entry, "service": "portal"})
               for _ in range(30)]
    challenger = ScriptedAgent(Side.CHALLENGER, script)
    passive = ScriptedAgent(Side.WARDEN, [])  # only HOLDs

    r1 = run_match("T0", 100, challenger, passive)
    r2 = run_match("T0", 100, ScriptedAgent(Side.CHALLENGER, script), ScriptedAgent(Side.WARDEN, []))
    assert r1.final_hash == r2.final_hash
    # with 30 attempts on a soft public service, a foothold is overwhelmingly likely
    card = score_match(r1.log)
    assert card.challenger["objective_progress"] >= 0.2
