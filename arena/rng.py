"""Deterministic random-number generation for the engine.

Determinism is a hard requirement (the CI "determinism gate"): a match is a
pure function of ``(scenario, seed, intents)``. We therefore funnel *all*
randomness through a single seeded generator and derive independent, named
sub-streams from it so that adding a new random draw in one subsystem cannot
shift the number stream consumed by another.

We rely on Python's ``random.Random`` (Mersenne Twister), whose output is
stable across platforms for a given CPython major version. The state hash
(see :mod:`arena.world`) is what the determinism gate actually compares, so
any accidental nondeterminism surfaces immediately as a hash mismatch.
"""

from __future__ import annotations

import hashlib
import random

__all__ = ["DeterministicRNG"]


def _derive_seed(root_seed: int, stream: str) -> int:
    """Derive a stable 64-bit sub-seed for a named stream from the root seed."""
    h = hashlib.sha256(f"{root_seed}:{stream}".encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big")


class DeterministicRNG:
    """A root seed plus lazily-created, independently-seeded named streams.

    Example::

        rng = DeterministicRNG(4471)
        rng.stream("engine.resolve").random()   # engine outcome rolls
        rng.stream("scenario.gen").random()      # world generation

    Two streams never interfere: consuming numbers from one leaves the other
    untouched, which keeps results comparable when only one subsystem changes.
    """

    def __init__(self, root_seed: int) -> None:
        self.root_seed = int(root_seed)
        self._streams: dict[str, random.Random] = {}

    def stream(self, name: str) -> random.Random:
        r = self._streams.get(name)
        if r is None:
            r = random.Random(_derive_seed(self.root_seed, name))
            self._streams[name] = r
        return r

    def chance(self, name: str, p: float) -> bool:
        """Return True with probability ``p`` on the named stream."""
        p = max(0.0, min(1.0, p))
        return self.stream(name).random() < p
