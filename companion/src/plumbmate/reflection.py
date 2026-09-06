"""Batch cognition over the memory store: relationship summaries and Reflexion.

Two jobs that run between turns, never inside one (NaviBrain #11 MemGPT/CLS,
#48 Reflexion; RFC-001 §8):

* ``refresh_relationship_summaries`` compresses what two sims have been
  through into one ``relationship_summary`` row per pair, regenerated at
  most weekly and only when new evidence arrived. The previous summary is
  superseded, never rewritten.
* ``reflect`` turns the retrieval log and open threads into one to three
  self-notes ("next time, bring this up"). Notes are ``episodic`` rows with
  ``event_kind='reflection'`` and trigger keywords, so they surface again
  only when their topic returns.

Everything written here is ``ai_inferred`` (confidence ≤ 0.9 by schema
CHECK). Nothing in this module can make canon: the only way up is
``MemoryStore.promote_to_canon`` with evidence.

The text is produced by a ``Summarizer``. ``RuleSummarizer`` is deterministic
and free; a model-backed summarizer would implement the same two methods and
charge the session budget one attempt per call. None exists yet on purpose:
the pipeline, storage and tests do not depend on which one is plugged in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Protocol

from .memory import MemoryStore
from .memory_text import clip, tokenize

SUMMARY_INTERVAL_DAYS = 7.0
SUMMARY_EVIDENCE_LIMIT = 8
SUMMARY_MIN_EVIDENCE = 3
SUMMARY_LOOKBACK_DAYS = 30.0
SUMMARY_CONFIDENCE = 0.8
SUMMARY_IMPORTANCE = 5
MAX_PAIR_SIMS = 6

REFLECTION_WINDOW_DAYS = 7.0
REFLECTION_CONFIDENCE = 0.6
REFLECTION_IMPORTANCE = 4
MAX_REFLECTIONS = 3
PROMISE_NAG_DAYS = 3.0
MISS_MIN_TOKENS = 2
MAX_TRIGGERS = 6

DAY = 86400.0


@dataclass(frozen=True)
class Evidence:
    memory_id: int
    summary: str
    memory_type: str
    event_kind: str | None
    importance: int
    status: str
    created_at: float


@dataclass(frozen=True)
class Finding:
    """One thing the reflection pass noticed; the summarizer phrases it."""
    kind: str                       # missed_recall | open_promise | disputed_inference
    subject: str                    # the query, promise or guess text
    source_ids: tuple[int, ...] = ()
    triggers: tuple[str, ...] = ()
    days: int = 0


class Summarizer(Protocol):
    def relationship(self, a_name: str, b_name: str, evidence: list[Evidence], *, now: float) -> str: ...
    def reflection(self, finding: Finding) -> str: ...


class RuleSummarizer:
    """Deterministic Korean phrasing; same input, same text (prompt-cache friendly)."""

    def relationship(self, a_name: str, b_name: str, evidence: list[Evidence], *, now: float) -> str:
        chats = sum(1 for e in evidence if e.event_kind in ("chat_turn", "chat_reply"))
        events = len(evidence) - chats
        oldest = min(e.created_at for e in evidence)
        span = max(1, int((now - oldest) / DAY) + 1)
        open_promises = sum(1 for e in evidence if e.memory_type == "promise" and e.status == "open")
        highlights = [clip(e.summary, 48) for e in evidence[:2]]
        parts = [f"{a_name}↔{b_name}: 최근 {span}일간 함께한 기억 {len(evidence)}건(대화 {chats}, 사건 {events})"]
        if highlights:
            parts.append("눈에 띄는 일: " + "; ".join(highlights))
        if open_promises:
            parts.append(f"지키지 못한 약속 {open_promises}건")
        return ". ".join(parts)

    def reflection(self, finding: Finding) -> str:
        subject = clip(finding.subject, 60)
        if finding.kind == "missed_recall":
            return f"'{subject}' 이야기가 나왔을 때 떠오른 기억이 없었다. 다음엔 그 일을 먼저 물어보자."
        if finding.kind == "open_promise":
            return f"'{subject}' 약속이 {finding.days}일째 열려 있다. 다음 대화에서 먼저 꺼내자."
        if finding.kind == "disputed_inference":
            return f"'{subject}' 추측은 정설과 어긋나 보류 중이다. 플레이어에게 확인해 보자."
        raise ValueError("Unknown finding kind: " + finding.kind)


def _evidence(rows: list[dict[str, Any]]) -> list[Evidence]:
    return [Evidence(int(r["memory_id"]), str(r["summary"]), str(r["memory_type"]), r["event_kind"],
                     int(r["importance"]), str(r["status"]), float(r["created_at_real"])) for r in rows]


def refresh_relationship_summaries(
    store: MemoryStore, session_id: str, *, summarizer: Summarizer | None = None,
    interval_days: float = SUMMARY_INTERVAL_DAYS, lookback_days: float = SUMMARY_LOOKBACK_DAYS,
    min_evidence: int = SUMMARY_MIN_EVIDENCE, now: float | None = None,
) -> list[int]:
    """Regenerate summaries for sim pairs that need one. Returns new row ids.

    A pair needs one when it has no active summary, or the existing one is
    older than ``interval_days`` and evidence arrived since it was written.
    An old summary of a pair with no recent evidence is archived.
    """
    summarizer = summarizer or RuleSummarizer()
    now = store._now() if now is None else now
    sims = store.session_sims(session_id)[:MAX_PAIR_SIMS]
    created: list[int] = []
    for (a, a_name, _), (b, b_name, _) in combinations(sims, 2):
        rows = store.pair_evidence(session_id, a, b, since=now - lookback_days * DAY, limit=SUMMARY_EVIDENCE_LIMIT)
        previous = store.relationship_summary(session_id, a, b)
        if previous is not None:
            written = float(previous["created_at_real"])
            if now - written < interval_days * DAY:
                continue
            if len(rows) < min_evidence:
                # The relationship went quiet: a summary that says "recently"
                # would mislead, so it retires instead of lingering.
                store.archive(int(previous["memory_id"]))
                continue
            if not any(float(r["created_at_real"]) > written for r in rows):
                continue
        elif len(rows) < min_evidence:
            continue
        evidence = _evidence(rows)
        text = summarizer.relationship(a_name, b_name, evidence, now=now)
        new_id = store.add_memory(
            session_id, "relationship_summary", text, provenance="ai_inferred",
            confidence=SUMMARY_CONFIDENCE, importance=SUMMARY_IMPORTANCE,
            entities={a: "actor", b: "target"}, source_ids=[e.memory_id for e in evidence],
            created_at=now,
        )
        if previous is not None:
            store.supersede(int(previous["memory_id"]), new_id)
        created.append(new_id)
    return created


def findings(store: MemoryStore, session_id: str, *, window_days: float = REFLECTION_WINDOW_DAYS,
             now: float | None = None) -> list[Finding]:
    """What the session left unresolved, most actionable first."""
    now = store._now() if now is None else now
    found: list[Finding] = []
    for row in store.rows_of(session_id, memory_type="promise", status="open"):
        age = int((now - float(row["created_at_real"])) / DAY)
        if age >= PROMISE_NAG_DAYS:
            found.append(Finding("open_promise", str(row["summary"]), (int(row["memory_id"]),),
                                 tuple(tokenize(row["summary"])[:MAX_TRIGGERS]), age))
    for row in store.rows_of(session_id, status="disputed", since=now - window_days * DAY):
        if row["provenance"] == "ai_inferred":
            found.append(Finding("disputed_inference", str(row["summary"]), (int(row["memory_id"]),),
                                 tuple(tokenize(row["summary"])[:MAX_TRIGGERS])))
    seen: set[tuple[str, ...]] = set()
    for miss in store.missed_turns(session_id, now - window_days * DAY):
        tokens = tuple(tokenize(miss["query"])[:MAX_TRIGGERS])
        if len(tokens) < MISS_MIN_TOKENS or tokens in seen:
            continue
        seen.add(tokens)
        found.append(Finding("missed_recall", str(miss["query"]), (), tokens))
    return found


def reflect(store: MemoryStore, session_id: str, *, summarizer: Summarizer | None = None,
            window_days: float = REFLECTION_WINDOW_DAYS, max_lines: int = MAX_REFLECTIONS,
            now: float | None = None) -> list[int]:
    """Write up to ``max_lines`` self-notes for the session. Returns new ids.

    A note whose text was already stored in the last week is not repeated,
    so running this every few turns is safe.
    """
    summarizer = summarizer or RuleSummarizer()
    now = store._now() if now is None else now
    created: list[int] = []
    for finding in findings(store, session_id, window_days=window_days, now=now):
        if len(created) >= max_lines:
            break
        text = summarizer.reflection(finding)
        if store.recently_stored(session_id, text, window_days=window_days):
            continue
        created.append(store.add_memory(
            session_id, "episodic", text, provenance="ai_inferred", confidence=REFLECTION_CONFIDENCE,
            importance=REFLECTION_IMPORTANCE, event_kind="reflection",
            trigger_keywords=list(finding.triggers) or None, details=finding.kind,
            source_ids=list(finding.source_ids) or None, created_at=now,
        ))
    return created


def maintain(store: MemoryStore, session_id: str | None = None, *,
             summarizer: Summarizer | None = None) -> dict[str, int]:
    """The between-turns batch: merge, archive, summarize, reflect.

    Runs for one session or, when ``session_id`` is None, every scoped
    session. Cheap and idempotent: repeated runs change nothing until new
    evidence arrives.
    """
    sessions = [session_id] if session_id else store.sessions()
    result = {"merged_groups": store.consolidate(session_id), "archived": store.archive_stale(),
              "summaries": 0, "reflections": 0}
    for scope in sessions:
        if not scope or scope == "unscoped":
            continue
        result["summaries"] += len(refresh_relationship_summaries(store, scope, summarizer=summarizer))
        result["reflections"] += len(reflect(store, scope, summarizer=summarizer))
    return result
