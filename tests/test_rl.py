"""RL env + tabular-Q learning agent tests."""

from __future__ import annotations

from arena.agents import RuleChallenger, RuleWarden
from arena.match import run_match
from arena.rl import N_ACTIONS, QWarden, WardenEnv, train_warden


def test_env_api_shape():
    env = WardenEnv("T1")
    obs, info = env.reset(seed=1)
    assert len(obs) == env.observation_dim
    obs2, reward, terminated, truncated, info = env.step(0)
    assert len(obs2) == env.observation_dim
    assert isinstance(reward, float)
    assert isinstance(terminated, bool) and isinstance(truncated, bool)


def test_env_is_deterministic():
    def rollout():
        env = WardenEnv("T1")
        env.reset(seed=7)
        total = 0.0
        for _ in range(30):
            _, r, term, trunc, _ = env.step(2)  # always INVESTIGATE
            total += r
            if term or trunc:
                break
        return round(total, 6)
    assert rollout() == rollout()


def test_training_is_deterministic():
    a = train_warden("T1", episodes=20, seeds=list(range(5)))
    b = train_warden("T1", episodes=20, seeds=list(range(5)))
    assert a.q == b.q


def test_qwarden_plays_and_is_valid():
    learner = train_warden("T1", episodes=40, seeds=list(range(8)))
    res = run_match("T1", 101, RuleChallenger(), QWarden(learner))
    assert res.ticks > 0
    # no rule violations: the decoded macro-actions are always schema-valid
    from arena.schema import EventType
    assert not res.log.events_of(EventType.INTENT_REJECTED.value)


def test_trained_warden_beats_or_matches_random_actions():
    """A trained policy should not be worse than always-HOLD on warden winrate."""
    learner = train_warden("T1", episodes=120, seeds=list(range(30)), epsilon=0.25)
    trained = sum(run_match("T1", s, RuleChallenger(), QWarden(learner)).winner == "warden"
                  for s in range(100, 115))
    from arena.agents import PassiveWarden
    passive = sum(run_match("T1", s, RuleChallenger(), PassiveWarden()).winner == "warden"
                  for s in range(100, 115))
    assert trained >= passive
