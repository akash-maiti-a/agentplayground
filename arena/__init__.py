"""ARENA — a sandboxed, fully-abstract cyber-defense sparring range.

Two AI agents spar inside a *simulated state machine*:

* the **Challenger** (a constrained challenge generator) tries to reach an
  objective inside a fictional network, and
* the **Warden** (a detection-and-response system) detects, contains and
  recovers.

The central safety property is that **the abstraction is the isolation**:
agents never receive a shell, a socket, a real network stack, or the ability
to run code. They emit typed, schema-validated *intents* and a deterministic
engine resolves them against fictional hosts whose "weaknesses" are abstract
numeric attributes. No step of a match maps to a real-world procedure, so
there is nothing that could be reused to compromise a real system.

See the project plan and README for the full design.
"""

__version__ = "0.1.0"
