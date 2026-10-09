"""Schema / rules-of-engagement unit tests."""

from __future__ import annotations

import pytest

from arena.schema import (
    AP_COST, ChallengerVerb, Intent, Side, WardenVerb, validate_intent,
)


def test_known_good_intents_validate():
    assert validate_intent(Intent(Side.CHALLENGER, "RECON", {"zone": "core"})).ok
    assert validate_intent(Intent(Side.WARDEN, "ISOLATE", {"node": "ledger-db"})).ok
    assert validate_intent(Intent(Side.WARDEN, "DECLARE",
                                  {"node": "x", "verdict": "benign"})).ok


def test_unknown_verb_rejected():
    assert not validate_intent(Intent(Side.CHALLENGER, "PWN", {})).ok


def test_wrong_side_rejected():
    assert not validate_intent(Intent(Side.CHALLENGER, "ISOLATE", {"node": "x"})).ok
    assert not validate_intent(Intent(Side.WARDEN, "ACCESS",
                                      {"node": "x", "service": "portal"})).ok


def test_arg_mismatch_rejected():
    assert not validate_intent(Intent(Side.CHALLENGER, "ACCESS", {"node": "x"})).ok
    assert not validate_intent(
        Intent(Side.CHALLENGER, "RECON", {"zone": "core", "extra": 1})).ok


def test_bad_verdict_rejected():
    assert not validate_intent(
        Intent(Side.WARDEN, "DECLARE", {"node": "x", "verdict": "haxxed"})).ok


def test_every_verb_has_a_cost():
    for v in list(ChallengerVerb) + list(WardenVerb):
        assert v.value in AP_COST
