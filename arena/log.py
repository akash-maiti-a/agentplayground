"""Append-only, content-hashed match log — the single source of truth.

Every event is appended with a running hash chain: each record stores the
hash of ``(previous_hash + canonical(event))``. Tampering with any earlier
record breaks the chain, which the scoring service and replay tooling can
detect. The log is the only thing scoring reads (scoring is strictly
post-hoc), so agents cannot see or game their score mid-match.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

__all__ = ["MatchLog"]

_GENESIS = "0" * 64


def _hash(prev: str, event: dict[str, Any]) -> str:
    blob = prev + json.dumps(event, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class MatchLog:
    match_id: str
    records: list[dict[str, Any]] = field(default_factory=list)
    _tip: str = _GENESIS

    def append(self, event: dict[str, Any]) -> None:
        self._tip = _hash(self._tip, event)
        self.records.append({"seq": len(self.records), "event": event, "chain": self._tip})

    def extend(self, events: Iterable[dict[str, Any]]) -> None:
        for e in events:
            self.append(e)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return (r["event"] for r in self.records)

    def __len__(self) -> int:
        return len(self.records)

    def events_of(self, event_type: str) -> list[dict[str, Any]]:
        return [r["event"] for r in self.records if r["event"].get("type") == event_type]

    def verify_chain(self) -> bool:
        prev = _GENESIS
        for r in self.records:
            prev = _hash(prev, r["event"])
            if r["chain"] != prev:
                return False
        return True

    # --- persistence (JSON Lines) -----------------------------------------
    def to_jsonl(self) -> str:
        return "\n".join(json.dumps(r, sort_keys=True, separators=(",", ":")) for r in self.records)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_jsonl(), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "MatchLog":
        text = Path(path).read_text(encoding="utf-8")
        log = cls(match_id="loaded")
        for line in text.splitlines():
            if not line.strip():
                continue
            log.records.append(json.loads(line))
        if log.records:
            log._tip = log.records[-1]["chain"]
            # recover match_id from the match_start event if present
            for r in log.records:
                if r["event"].get("type") == "match_start":
                    log.match_id = r["event"].get("match_id", "loaded")
                    break
        return log
