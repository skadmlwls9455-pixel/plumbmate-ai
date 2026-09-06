"""Companion-side long-term memory: SQLite store, rule-based recall, audit log.

This is the Phase 1 vertical slice of the memory design (aiden RFC-001):
observed things become rows with a provenance, a small rule-based scorer picks
three to six of them for a turn, and every pick is explained in
``retrieval_log``. There are no embeddings and no model calls in this module.

What it does NOT do, on purpose:

- It never promotes anything to canon. ``ai_inferred`` rows are capped at
  confidence 0.9 by a CHECK constraint and nothing here writes them yet.
- It does not verify save scope. Rows are keyed by the request's
  ``session_id``; whether recall is allowed at all is the engine's decision
  (opt-in) until save receipts exist.
- It never deletes. Every lifecycle transition is a status change.

The game side never imports this: SQLite lives in the companion process only.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .memory_text import clip, fts_query, pack_lines, tokenize

MEMORY_TYPES = ("canon", "episodic", "sentiment", "promise", "relationship_summary")
PROVENANCES = ("game_observed", "bridge_observed", "player_canon", "ai_inferred")
STATUSES = ("active", "open", "resolved", "disputed", "superseded", "merged", "archived", "deleted")
RECALLABLE_STATUSES = ("active", "open")

UNSCOPED_SESSION = "unscoped"
MAX_SUMMARY_CHARS = 1600   # request schema: memory_summary maxLength
MAX_LINE_CHARS = 240
MAX_CANDIDATES = 50
MAX_QUERY_TOKENS = 64
LOGGED_CANDIDATES = 20

# Default importance per event kind (RFC-001 §7). Heartbeats are transport
# noise and are never stored.
IMPORTANCE_BY_EVENT_KIND = {
    "zone_loaded": 3,
    "save_begin": 1,
    "save_committed": 1,
    "interaction_started": 2,
    "chat_turn": 3,
    "chat_reply": 2,
}
SKIPPED_EVENT_KINDS = frozenset(("heartbeat",))

# Interim recency clock: real days, because the game does not report sim time
# yet. Half-lives follow RFC-001 §4.3 in spirit; canon and open promises never
# decay.
HALF_LIFE_DAYS = {"episodic": 7.0, "sentiment": 21.0, "relationship_summary": 30.0}

# Lifecycle defaults (RFC-001 §8/§9). Everything is a status transition; the
# only physical DELETE is purge_deleted(), an explicit two-step action.
MERGE_WINDOW_DAYS = 3.0
MERGE_MIN_COUNT = 3
ARCHIVE_AFTER_DAYS = 90.0
ARCHIVE_MAX_IMPORTANCE = 2
INFERENCE_TTL_DAYS = 60.0
# Repeated *identical* observations are worth folding; distinct utterances are
# not, so chat turns and adapter-reported events are never merged.
UNMERGEABLE_EVENT_KINDS = frozenset(("chat_turn", "chat_reply", "recent_event", "reflection"))
# Derived rows restate observed evidence; the canon-contradiction heuristic
# would quarantine a whole summary for one quoted line, so they skip it.
DERIVED_KINDS = frozenset(("reflection", "relationship_summary"))
EVIDENCE_KINDS = ("player_confirm", "game_event")
# Contradiction heuristic for ai_inferred rows: high keyword overlap with a
# canon row plus an opposing polarity token. Deliberately narrow — it must
# never quarantine a merely *different* inference. Korean stems match by
# prefix (inflection), English words match whole; a following 않-token flips
# the last polarity seen ("좋아하지 않는다" reads as dislike). The 1-character
# negation 안 is dropped by the tokenizer and is a known miss (RFC-001 F2).
_POLARITY_PAIRS = (
    (("좋아", "사랑"), ("싫어", "미워"), "prefix"),
    (("있다", "있어", "있는"), ("없다", "없어", "없는"), "prefix"),
    (("맞다", "맞아"), ("틀리", "틀렸"), "prefix"),
    (("good",), ("bad",), "word"),
    (("likes", "like", "loves", "love"), ("dislikes", "dislike", "hates", "hate"), "word"),
    (("true",), ("false",), "word"),
)
_NEGATION_PREFIXES = ("않",)
INFERRED_PREFIX = "[에이든의 추측] "

ROLE_WEIGHTS = {"actor": 1.0, "target": 1.0, "witness": 0.6, "mentioned": 0.4, "location": 0.5}
# Per-turn caps keep one type from crowding the others out. Episodic rows are
# the only type this slice produces, so they are bounded by max_pick alone;
# RFC-001's "two per type" was written for the full type mix.
TYPE_CAPS = {"canon": 3, "sentiment": 2, "promise": 2, "relationship_summary": 1}

# RFC-001 §4.3 weights, with w_imp raised from 1.0 to 1.5: at 1.0 a trivial
# event from yesterday (imp 1, rec≈0.9) outscored a major one from months ago
# (imp 9, rec≈0), which is the opposite of what T5 asks for.
DEFAULT_CONFIG = {
    "w_kw": 2.0, "w_ent": 1.5, "w_imp": 1.5, "w_rec": 1.2, "w_open": 0.8, "w_loc": 0.5,
    "pin_bonus": 1.5, "score_floor": 1.0, "max_pick": 6, "min_pick": 3,
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
  entity_id  INTEGER PRIMARY KEY,
  kind       TEXT NOT NULL CHECK (kind IN ('sim','pet','household','lot','object','concept')),
  game_ref   TEXT,
  name       TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_ref ON entities(kind, game_ref) WHERE game_ref IS NOT NULL;
CREATE TABLE IF NOT EXISTS entity_aliases (
  entity_id INTEGER NOT NULL REFERENCES entities(entity_id),
  alias     TEXT NOT NULL,
  source    TEXT NOT NULL CHECK (source IN ('game','player','ai_learned')),
  PRIMARY KEY (entity_id, alias)
);
CREATE TABLE IF NOT EXISTS memories (
  memory_id       INTEGER PRIMARY KEY,
  session_id      TEXT NOT NULL,
  memory_type     TEXT NOT NULL CHECK (memory_type IN ('canon','episodic','sentiment','promise','relationship_summary')),
  provenance      TEXT NOT NULL CHECK (provenance IN ('game_observed','bridge_observed','player_canon','ai_inferred')),
  confidence      REAL NOT NULL DEFAULT 1.0
                    CHECK (confidence BETWEEN 0 AND 1)
                    CHECK (provenance != 'ai_inferred' OR confidence <= 0.9),
  importance      INTEGER NOT NULL DEFAULT 3 CHECK (importance BETWEEN 1 AND 10),
  status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN
                    ('active','open','resolved','disputed','superseded','merged','archived','deleted')),
  pinned          INTEGER NOT NULL DEFAULT 0,
  event_kind      TEXT,
  source_sim_id   INTEGER REFERENCES entities(entity_id),
  target_sim_id   INTEGER REFERENCES entities(entity_id),
  emotion         TEXT,
  valence         REAL CHECK (valence IS NULL OR valence BETWEEN -1 AND 1),
  intensity       REAL CHECK (intensity IS NULL OR intensity BETWEEN 0 AND 1),
  summary         TEXT NOT NULL,
  details         TEXT,
  keywords        TEXT NOT NULL DEFAULT '',
  trigger_keywords TEXT,
  sim_abs_day     INTEGER,
  created_at_real REAL NOT NULL,
  merged_into     INTEGER REFERENCES memories(memory_id),
  superseded_by   INTEGER REFERENCES memories(memory_id),
  source_ids      TEXT,
  recall_count    INTEGER NOT NULL DEFAULT 0,
  last_recalled_at REAL
);
CREATE INDEX IF NOT EXISTS idx_mem_scope ON memories(session_id, status, memory_type);
CREATE INDEX IF NOT EXISTS idx_mem_time ON memories(session_id, created_at_real);
CREATE TABLE IF NOT EXISTS memory_entities (
  memory_id INTEGER NOT NULL REFERENCES memories(memory_id),
  entity_id INTEGER NOT NULL REFERENCES entities(entity_id),
  role      TEXT NOT NULL CHECK (role IN ('actor','target','witness','location','mentioned')),
  PRIMARY KEY (memory_id, entity_id, role)
);
CREATE INDEX IF NOT EXISTS idx_me_entity ON memory_entities(entity_id);
CREATE TABLE IF NOT EXISTS retrieval_log (
  turn_id    TEXT NOT NULL,
  memory_id  INTEGER NOT NULL,
  selected   INTEGER NOT NULL,
  score      REAL NOT NULL,
  breakdown  TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY (turn_id, memory_id)
);
CREATE TABLE IF NOT EXISTS retrieval_turns (
  turn_id    TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  query      TEXT NOT NULL,
  candidates INTEGER NOT NULL,
  selected   INTEGER NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rt_session ON retrieval_turns(session_id, created_at);
CREATE TABLE IF NOT EXISTS canon_promotions (
  memory_id      INTEGER NOT NULL REFERENCES memories(memory_id),
  old_provenance TEXT NOT NULL,
  evidence_kind  TEXT NOT NULL CHECK (evidence_kind IN ('player_confirm','game_event')),
  evidence_ref   TEXT NOT NULL,
  promoted_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retrieval_config (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  w_kw REAL NOT NULL, w_ent REAL NOT NULL, w_imp REAL NOT NULL, w_rec REAL NOT NULL,
  w_open REAL NOT NULL, w_loc REAL NOT NULL, pin_bonus REAL NOT NULL,
  score_floor REAL NOT NULL, max_pick INTEGER NOT NULL, min_pick INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class Recalled:
    memory_id: int
    memory_type: str
    provenance: str
    summary: str
    score: float
    breakdown: dict[str, float]
    reason: str

    def line(self) -> str:
        prefix = "[에이든의 추측] " if self.provenance == "ai_inferred" else ""
        return prefix + self.summary


class MemoryStore:
    """One SQLite file per bridge directory; safe for a single thread."""

    def __init__(self, path: Path | str, *, now: Callable[[], float] | None = None) -> None:
        self.path = Path(path)
        self._now = now or time.time
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(_SCHEMA)
        self._db.execute(
            "INSERT OR IGNORE INTO retrieval_config (id, w_kw, w_ent, w_imp, w_rec, w_open, w_loc,"
            " pin_bonus, score_floor, max_pick, min_pick) VALUES (1,?,?,?,?,?,?,?,?,?,?)",
            (DEFAULT_CONFIG["w_kw"], DEFAULT_CONFIG["w_ent"], DEFAULT_CONFIG["w_imp"],
             DEFAULT_CONFIG["w_rec"], DEFAULT_CONFIG["w_open"], DEFAULT_CONFIG["w_loc"],
             DEFAULT_CONFIG["pin_bonus"], DEFAULT_CONFIG["score_floor"],
             DEFAULT_CONFIG["max_pick"], DEFAULT_CONFIG["min_pick"]),
        )
        self.fts_available = self._enable_fts()
        self._db.commit()

    def _enable_fts(self) -> bool:
        try:
            self._db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
                "summary, keywords, content='', tokenize='unicode61')"
            )
            return True
        except sqlite3.OperationalError:
            # FTS5 missing from this SQLite build: keyword matching degrades to LIKE.
            return False

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- config

    def config(self) -> dict[str, float]:
        row = self._db.execute("SELECT * FROM retrieval_config WHERE id = 1").fetchone()
        return {key: row[key] for key in row.keys() if key != "id"}

    def set_config(self, **values: float) -> None:
        unknown = set(values) - set(DEFAULT_CONFIG)
        if unknown:
            raise ValueError("Unknown retrieval config keys: " + ", ".join(sorted(unknown)))
        for key, value in values.items():
            self._db.execute(f"UPDATE retrieval_config SET {key} = ? WHERE id = 1", (value,))
        self._db.commit()

    # -------------------------------------------------------------- entities

    def ensure_entity(self, kind: str, game_ref: str | None, name: str,
                      alias_source: str = "game") -> int:
        name = clip(name, 128) or "Unknown"
        row = None
        if game_ref is not None:
            row = self._db.execute(
                "SELECT entity_id FROM entities WHERE kind = ? AND game_ref = ?", (kind, str(game_ref)),
            ).fetchone()
        if row is None:
            cursor = self._db.execute(
                "INSERT INTO entities (kind, game_ref, name, created_at) VALUES (?,?,?,?)",
                (kind, None if game_ref is None else str(game_ref), name, self._now()),
            )
            entity_id = int(cursor.lastrowid)
        else:
            entity_id = int(row["entity_id"])
        self.add_alias(entity_id, name, alias_source)
        return entity_id

    def add_alias(self, entity_id: int, alias: str, source: str = "player") -> None:
        alias = clip(alias, 128).lower()
        if alias:
            self._db.execute(
                "INSERT OR IGNORE INTO entity_aliases (entity_id, alias, source) VALUES (?,?,?)",
                (entity_id, alias, source),
            )

    def _request_entities(self, request: dict[str, Any]) -> dict[int, str]:
        """Entity ids for the actor and household of a request (ensured)."""
        found: dict[int, str] = {}
        actor = request.get("actor") or {}
        if isinstance(actor, dict) and "sim_id" in actor:
            found[self.ensure_entity("sim", str(actor["sim_id"]), str(actor.get("name", "")))] = "actor"
        for member in request.get("household") or []:
            if isinstance(member, dict) and "sim_id" in member:
                entity_id = self.ensure_entity("sim", str(member["sim_id"]), str(member.get("name", "")))
                found.setdefault(entity_id, "witness")
        return found

    def _mentioned_entities(self, tokens: list[str]) -> set[int]:
        """Entities whose alias tokens all appear in the text tokens."""
        if not tokens:
            return set()
        token_set = set(tokens)
        mentioned: set[int] = set()
        for row in self._db.execute("SELECT entity_id, alias FROM entity_aliases"):
            alias_tokens = tokenize(row["alias"])
            if alias_tokens and all(token in token_set for token in alias_tokens):
                mentioned.add(int(row["entity_id"]))
        return mentioned

    # ----------------------------------------------------------------- write

    def add_memory(
        self,
        session_id: str,
        memory_type: str,
        summary: str,
        *,
        provenance: str = "game_observed",
        importance: int = 3,
        confidence: float = 1.0,
        event_kind: str | None = None,
        status: str = "active",
        entities: dict[int, str] | None = None,
        details: str | None = None,
        trigger_keywords: list[str] | None = None,
        pinned: bool = False,
        created_at: float | None = None,
        source_ids: list[int] | None = None,
        commit: bool = True,
    ) -> int:
        if memory_type not in MEMORY_TYPES:
            raise ValueError("Unknown memory type: " + memory_type)
        if provenance not in PROVENANCES:
            raise ValueError("Unknown provenance: " + provenance)
        # The recall line carries a prefix for inferences; keep the whole
        # line within the per-line budget so packing never truncates it.
        summary = clip(summary, MAX_LINE_CHARS - (len(INFERRED_PREFIX) if provenance == "ai_inferred" else 0))
        if not summary:
            raise ValueError("A memory needs a summary")
        keywords = tokenize(summary + " " + (details or ""))
        cursor = self._db.execute(
            "INSERT INTO memories (session_id, memory_type, provenance, confidence, importance,"
            " status, pinned, event_kind, summary, details, keywords, trigger_keywords, created_at_real,"
            " source_ids) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (session_id, memory_type, provenance, float(confidence), int(max(1, min(10, importance))),
             status, 1 if pinned else 0, event_kind, summary, details, " ".join(keywords),
             json.dumps(trigger_keywords, ensure_ascii=False) if trigger_keywords else None,
             self._now() if created_at is None else float(created_at),
             json.dumps([int(i) for i in source_ids]) if source_ids else None),
        )
        memory_id = int(cursor.lastrowid)
        if self.fts_available:
            self._db.execute(
                "INSERT INTO memories_fts (rowid, summary, keywords) VALUES (?,?,?)",
                (memory_id, summary, " ".join(keywords)),
            )
        links = dict(entities or {})
        for entity_id in self._mentioned_entities(keywords):
            links.setdefault(entity_id, "mentioned")
        for entity_id, role in links.items():
            self._db.execute(
                "INSERT OR IGNORE INTO memory_entities (memory_id, entity_id, role) VALUES (?,?,?)",
                (memory_id, entity_id, role),
            )
        derived = memory_type in DERIVED_KINDS or event_kind in DERIVED_KINDS
        if provenance == "ai_inferred" and status == "active" and not derived:
            conflict = self._contradicts_canon(session_id, keywords)
            if conflict is not None:
                # Quarantined, not deleted: it stays inspectable but never recalled.
                self._db.execute(
                    "UPDATE memories SET status = 'disputed', details = COALESCE(details, '') || ? WHERE memory_id = ?",
                    (f" [disputes canon #{conflict}]", memory_id),
                )
        if commit:
            self._db.commit()
        return memory_id

    def _contradicts_canon(self, session_id: str, keywords: list[str]) -> int | None:
        """Return the canon memory_id an inference contradicts, if the
        heuristic fires: ≥60% of the canon row's content keywords appear in
        the inference AND the two carry opposite polarity on the same pair.
        An inference that carries both sides itself is left alone."""
        tokens = set(keywords)
        polarity_new = _polarities(keywords)
        if not polarity_new or _two_sided(polarity_new):
            return None
        rows = self._db.execute(
            "SELECT memory_id, keywords FROM memories WHERE session_id = ? AND memory_type = 'canon'"
            " AND status = 'active'", (session_id,),
        ).fetchall()
        for row in rows:
            canon_keywords = row["keywords"].split()
            content = {t for t in canon_keywords if _polarity_of(t) is None and not t.startswith(_NEGATION_PREFIXES)}
            if not content:
                continue
            overlap = len(content & tokens) / len(content)
            if overlap < 0.6:
                continue
            polarity_canon = _polarities(canon_keywords)
            if _two_sided(polarity_canon):
                continue
            if any((pair, "a") in polarity_canon and (pair, "b") in polarity_new
                   or (pair, "b") in polarity_canon and (pair, "a") in polarity_new
                   for pair in range(len(_POLARITY_PAIRS))):
                return int(row["memory_id"])
        return None

    def remember_turn_request(self, request: dict[str, Any]) -> list[int]:
        """Store what the player said (an observed utterance) and any events
        the adapter reported with the turn. Returns the new memory ids."""
        session_id = str(request.get("session_id") or UNSCOPED_SESSION)
        entities = self._request_entities(request)
        actor = request.get("actor")
        actor_name = clip(str(actor.get("name") or "플레이어") if isinstance(actor, dict) else "플레이어", 64)
        created: list[int] = []
        message = clip(str(request.get("player_message") or ""), 200)
        if message:
            created.append(self.add_memory(
                session_id, "episodic", f"{actor_name}: {message}",
                provenance="bridge_observed", importance=IMPORTANCE_BY_EVENT_KIND["chat_turn"],
                event_kind="chat_turn", entities=entities,
            ))
        events = request.get("recent_events")
        for event in (events if isinstance(events, list) else []):
            text = clip(str(event or ""), MAX_LINE_CHARS)
            if not text or self._has_recent_duplicate(session_id, text):
                continue
            created.append(self.add_memory(
                session_id, "episodic", text, provenance="game_observed", importance=3,
                event_kind="recent_event", entities=entities,
            ))
        return created

    def remember_turn_response(self, request: dict[str, Any], response: dict[str, Any]) -> int | None:
        session_id = str(request.get("session_id") or UNSCOPED_SESSION)
        message = clip(str(response.get("partner_message") or ""), 200)
        if not message:
            return None
        entities = self._request_entities(request)
        return self.add_memory(
            session_id, "episodic", f"동반 심: {message}",
            provenance="bridge_observed", importance=IMPORTANCE_BY_EVENT_KIND["chat_reply"],
            event_kind="chat_reply", entities=entities,
        )

    def remember_event(self, kind: str, payload: dict[str, Any] | None,
                       session_id: str | None = None) -> int | None:
        """Store one socket event. Heartbeats (incl. keepalives) are skipped.
        Events carry no session unless the payload or caller says so, and
        unscoped rows are never recalled — only kept for audit."""
        if kind in SKIPPED_EVENT_KINDS:
            return None
        payload = dict(payload) if isinstance(payload, dict) else ({"value": payload} if payload is not None else {})
        scope = str(session_id or payload.get("session_id") or UNSCOPED_SESSION)
        entities: dict[int, str] = {}
        if "sim_id" in payload:
            entities[self.ensure_entity("sim", str(payload["sim_id"]), str(payload.get("name", "")))] = "actor"
        if "target_sim_id" in payload:
            entities[self.ensure_entity("sim", str(payload["target_sim_id"]), str(payload.get("target_name", "")))] = "target"
        detail = json.dumps({str(k): v for k, v in payload.items() if k != "session_id"},
                            ensure_ascii=False, sort_keys=True, default=str)
        summary = clip(f"{kind}: {detail}" if detail != "{}" else kind, MAX_LINE_CHARS)
        return self.add_memory(
            scope, "episodic", summary, provenance="game_observed",
            importance=IMPORTANCE_BY_EVENT_KIND.get(kind, 2), event_kind=kind, entities=entities,
        )

    def _has_recent_duplicate(self, session_id: str, summary: str, window_seconds: float = 86400.0) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM memories WHERE session_id = ? AND summary = ? AND created_at_real >= ? LIMIT 1",
            (session_id, summary, self._now() - window_seconds),
        ).fetchone()
        return row is not None

    # ---------------------------------------------------------------- recall

    def recall(self, request: dict[str, Any], *, log: bool = True) -> list[Recalled]:
        """Score candidates for this turn and pick three to six (RFC-001 §4)."""
        session_id = str(request.get("session_id") or UNSCOPED_SESSION)
        if session_id == UNSCOPED_SESSION:
            return []
        query_text = " ".join([str(request.get("player_message", ""))]
                              + [str(item) for item in request.get("recent_events") or []])
        tokens = tokenize(query_text)[:MAX_QUERY_TOKENS]
        present = self._request_entities(request)
        scored = self._score_candidates(session_id, tokens, present)
        # The adapter repeats recent_events on every turn; the rows they
        # created would otherwise recall themselves and spend slots on text
        # the model already sees in the request.
        already_in_request = {clip(str(item), MAX_LINE_CHARS) for item in request.get("recent_events") or []}
        scored = [item for item in scored if item.summary not in already_in_request]
        selected = self._select(scored)
        if log:
            turn_id = str(request.get("request_id") or "unknown")
            self._log(turn_id, scored, selected)
            self._db.execute(
                "INSERT OR REPLACE INTO retrieval_turns (turn_id, session_id, query, candidates, selected, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (turn_id, session_id, clip(query_text, MAX_LINE_CHARS), len(scored), len(selected), self._now()),
            )
            self._db.commit()
        return selected

    def recall_summary(self, request: dict[str, Any]) -> str:
        return pack_lines(
            [item.line() for item in self.recall(request)],
            max_chars=MAX_SUMMARY_CHARS, max_line_chars=MAX_LINE_CHARS,
        )

    def search(self, text: str, session_id: str | None = None, limit: int = 10) -> list[Recalled]:
        """Developer lookup: same scorer, no entity context, no log."""
        tokens = tokenize(text)
        scored = self._score_candidates(session_id, tokens, {}, floor=0.0)
        return scored[:limit]

    def _keyword_scores(self, session_id: str | None, tokens: list[str]) -> dict[int, float]:
        """Keyword score = share of query tokens present in the memory's keywords.

        FTS5 (or LIKE without it) only proposes candidates; the score itself is
        coverage, not bm25. Rank-normalizing bm25 across a handful of
        candidates polarized them (one at 1.0, another at 0.0) and let
        document length, not relevance, decide the turn.
        """
        if not tokens:
            return {}
        if self.fts_available:
            rows = self._db.execute(
                "SELECT m.memory_id, m.keywords FROM memories_fts f"
                " JOIN memories m ON m.memory_id = f.rowid"
                " WHERE memories_fts MATCH ? AND (? IS NULL OR m.session_id = ?)"
                " AND m.status IN ('active','open') ORDER BY bm25(memories_fts) LIMIT ?",
                (fts_query(tokens), session_id, session_id, MAX_CANDIDATES),
            ).fetchall()
        else:
            clauses = " OR ".join("keywords LIKE ?" for _ in tokens)
            rows = self._db.execute(
                f"SELECT memory_id, keywords FROM memories WHERE ({clauses})"
                " AND (? IS NULL OR session_id = ?) AND status IN ('active','open') LIMIT ?",
                tuple("%" + token + "%" for token in tokens) + (session_id, session_id, MAX_CANDIDATES),
            ).fetchall()
        scores: dict[int, float] = {}
        for row in rows:
            present = set(row["keywords"].split())
            hits = sum(1 for token in tokens if token in present)
            if hits:
                scores[int(row["memory_id"])] = hits / len(tokens)
        return scores

    def _score_candidates(self, session_id: str | None, tokens: list[str],
                          present: dict[int, str], floor: float | None = None) -> list[Recalled]:
        config = self.config()
        if floor is None:
            floor = config["score_floor"]
        keyword = self._keyword_scores(session_id, tokens)
        candidate_ids = set(keyword)
        if present:
            placeholders = ",".join("?" for _ in present)
            rows = self._db.execute(
                f"SELECT DISTINCT me.memory_id FROM memory_entities me JOIN memories m"
                f" ON m.memory_id = me.memory_id WHERE me.entity_id IN ({placeholders})"
                " AND (? IS NULL OR m.session_id = ?) AND m.status IN ('active','open')"
                " ORDER BY m.created_at_real DESC LIMIT ?",
                tuple(present) + (session_id, session_id, MAX_CANDIDATES),
            ).fetchall()
            candidate_ids.update(int(row["memory_id"]) for row in rows)
        if not candidate_ids:
            return []
        placeholders = ",".join("?" for _ in candidate_ids)
        rows = self._db.execute(
            f"SELECT * FROM memories WHERE memory_id IN ({placeholders})", tuple(candidate_ids),
        ).fetchall()
        now = self._now()
        overlap = self._entity_overlaps([int(row["memory_id"]) for row in rows], present)
        scored: list[Recalled] = []
        for row in rows:
            memory_id = int(row["memory_id"])
            kw = keyword.get(memory_id, 0.0)
            ent = overlap.get(memory_id, 0.0)
            imp = row["importance"] / 10.0
            rec = self._recency(row, now)
            open_score = 0.0
            triggers = json.loads(row["trigger_keywords"]) if row["trigger_keywords"] else []
            triggered = any(str(trigger).lower() in tokens for trigger in triggers)
            if row["memory_type"] == "promise" and row["status"] == "open":
                open_score = 1.5 if triggered else 1.0
            elif row["event_kind"] == "reflection" and triggered:
                # A self-note earns its slot only when its topic comes back.
                open_score = 1.0
            loc = 0.0
            conf = float(row["confidence"]) if row["provenance"] == "ai_inferred" else 1.0
            score = conf * (
                config["w_kw"] * kw + config["w_ent"] * ent + config["w_imp"] * imp
                + config["w_rec"] * rec + config["w_open"] * open_score + config["w_loc"] * loc
            ) + (config["pin_bonus"] if row["pinned"] else 0.0)
            breakdown = {"kw": round(kw, 3), "ent": round(ent, 3), "imp": round(imp, 3),
                         "rec": round(rec, 3), "open": open_score, "loc": loc, "conf": conf,
                         "pin": config["pin_bonus"] if row["pinned"] else 0.0}
            scored.append(Recalled(
                memory_id, row["memory_type"], row["provenance"], row["summary"],
                round(score, 4), breakdown, _reason(breakdown, row["event_kind"]),
            ))
        scored.sort(key=lambda item: (-item.score, item.memory_id))
        return [item for item in scored if item.score >= floor]

    def _entity_overlaps(self, memory_ids: list[int], present: dict[int, str]) -> dict[int, float]:
        """Per memory: mean role weight of its links to entities in the
        request, over all its links. A row whose only links are household
        witnesses scores 0.6, one about the actor 1.0, so 'everyone was
        there' does not by itself outrank 'this was about you'."""
        if not memory_ids or not present:
            return {}
        placeholders = ",".join("?" for _ in memory_ids)
        rows = self._db.execute(
            f"SELECT memory_id, entity_id, role FROM memory_entities WHERE memory_id IN ({placeholders})",
            tuple(memory_ids),
        ).fetchall()
        links: dict[int, int] = {}
        matched: dict[int, float] = {}
        for row in rows:
            memory_id = int(row["memory_id"])
            links[memory_id] = links.get(memory_id, 0) + 1
            if int(row["entity_id"]) in present:
                matched[memory_id] = matched.get(memory_id, 0.0) + ROLE_WEIGHTS.get(row["role"], 0.4)
        return {memory_id: min(1.0, matched.get(memory_id, 0.0) / count) for memory_id, count in links.items()}

    def _recency(self, row: sqlite3.Row, now: float) -> float:
        if row["memory_type"] == "canon" or (row["memory_type"] == "promise" and row["status"] == "open"):
            return 1.0
        half_life = HALF_LIFE_DAYS.get(row["memory_type"], 7.0)
        age_days = max(0.0, now - float(row["created_at_real"])) / 86400.0
        return math.exp(-math.log(2.0) * age_days / half_life)

    def _select(self, scored: list[Recalled]) -> list[Recalled]:
        config = self.config()
        max_pick = int(config["max_pick"])
        picked: list[Recalled] = []
        per_type: dict[str, int] = {}
        for item in scored:
            if len(picked) >= max_pick:
                break
            cap = TYPE_CAPS.get(item.memory_type, max_pick)
            if per_type.get(item.memory_type, 0) >= cap:
                continue
            picked.append(item)
            per_type[item.memory_type] = per_type.get(item.memory_type, 0) + 1
        return picked

    def _log(self, turn_id: str, scored: list[Recalled], selected: list[Recalled]) -> None:
        chosen = {item.memory_id for item in selected}
        now = self._now()
        for item in scored[:LOGGED_CANDIDATES]:
            self._db.execute(
                "INSERT OR REPLACE INTO retrieval_log (turn_id, memory_id, selected, score, breakdown, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (turn_id, item.memory_id, 1 if item.memory_id in chosen else 0, item.score,
                 json.dumps({"breakdown": item.breakdown, "reason": item.reason}, ensure_ascii=False), now),
            )
        if chosen:
            placeholders = ",".join("?" for _ in chosen)
            self._db.execute(
                f"UPDATE memories SET recall_count = recall_count + 1, last_recalled_at = ?"
                f" WHERE memory_id IN ({placeholders})", (now, *chosen),
            )
        self._db.commit()

    # ------------------------------------------------------------- lifecycle

    def get(self, memory_id: int) -> dict[str, Any] | None:
        row = self._db.execute("SELECT * FROM memories WHERE memory_id = ?", (memory_id,)).fetchone()
        return dict(row) if row is not None else None

    def _require(self, memory_id: int) -> sqlite3.Row:
        row = self._db.execute("SELECT * FROM memories WHERE memory_id = ?", (memory_id,)).fetchone()
        if row is None:
            raise KeyError(f"No memory {memory_id}")
        return row

    def pin(self, memory_id: int, pinned: bool = True) -> None:
        """Pinned rows are exempt from decay, merging, and archiving."""
        self._require(memory_id)
        self._db.execute("UPDATE memories SET pinned = ? WHERE memory_id = ?", (1 if pinned else 0, memory_id))
        self._db.commit()

    def edit(self, memory_id: int, summary: str, details: str | None = None) -> int:
        """Correct a memory by superseding it: the old row keeps its history."""
        old = self._require(memory_id)
        if old["status"] in ("superseded", "merged", "deleted"):
            raise ValueError(f"Memory {memory_id} is {old['status']} and cannot be edited")
        links = {int(r["entity_id"]): r["role"] for r in self._db.execute(
            "SELECT entity_id, role FROM memory_entities WHERE memory_id = ?", (memory_id,))}
        # A corrected guess starts over as active and is re-checked against canon.
        status = "active" if old["status"] == "disputed" else old["status"]
        try:
            new_id = self.add_memory(
                old["session_id"], old["memory_type"], summary,
                provenance=old["provenance"], importance=old["importance"], confidence=old["confidence"],
                event_kind=old["event_kind"], status=status, entities=links,
                details=details if details is not None else old["details"],
                trigger_keywords=json.loads(old["trigger_keywords"]) if old["trigger_keywords"] else None,
                pinned=bool(old["pinned"]), created_at=old["created_at_real"],
                source_ids=json.loads(old["source_ids"]) if old["source_ids"] else None, commit=False,
            )
            # The correction inherits the recall history, or archive_stale
            # would retire an old row's fresh correction as never recalled.
            self._db.execute(
                "UPDATE memories SET recall_count = ?, last_recalled_at = ? WHERE memory_id = ?",
                (old["recall_count"], old["last_recalled_at"], new_id),
            )
            self._db.execute(
                "UPDATE memories SET status = 'superseded', superseded_by = ? WHERE memory_id = ?", (new_id, memory_id),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return new_id

    def soft_delete(self, memory_id: int) -> None:
        """Step one of deletion: hidden from every recall, row retained."""
        self._require(memory_id)
        self._db.execute("UPDATE memories SET status = 'deleted' WHERE memory_id = ?", (memory_id,))
        self._db.commit()

    def purge_deleted(self) -> int:
        """Step two, explicit: physically remove rows already soft-deleted."""
        rows = self._db.execute(
            "SELECT memory_id, summary, keywords FROM memories WHERE status = 'deleted'").fetchall()
        try:
            for row in rows:
                memory_id = int(row["memory_id"])
                # Rows that pointed here keep their status but lose the dangling link.
                self._db.execute("UPDATE memories SET merged_into = NULL WHERE merged_into = ?", (memory_id,))
                self._db.execute("UPDATE memories SET superseded_by = NULL WHERE superseded_by = ?", (memory_id,))
                self._db.execute("DELETE FROM memory_entities WHERE memory_id = ?", (memory_id,))
                self._db.execute("DELETE FROM retrieval_log WHERE memory_id = ?", (memory_id,))
                self._db.execute("DELETE FROM canon_promotions WHERE memory_id = ?", (memory_id,))
                if self.fts_available:
                    # A contentless table needs the original values to drop its postings.
                    self._db.execute(
                        "INSERT INTO memories_fts (memories_fts, rowid, summary, keywords) VALUES ('delete', ?, ?, ?)",
                        (memory_id, row["summary"], row["keywords"]),
                    )
                self._db.execute("DELETE FROM memories WHERE memory_id = ?", (memory_id,))
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return len(rows)

    def promote_to_canon(self, memory_id: int, evidence_kind: str, evidence_ref: str) -> None:
        """The single gate through which an inference may become canon.

        Evidence is mandatory and is recorded forever in canon_promotions.
        Repetition, recall count, or age never count as evidence.
        """
        if evidence_kind not in EVIDENCE_KINDS:
            raise ValueError("evidence_kind must be one of " + ", ".join(EVIDENCE_KINDS))
        evidence_ref = clip(str(evidence_ref or ""), 500)
        if not evidence_ref:
            raise ValueError("Promotion requires evidence: a player confirmation or a game event id")
        row = self._require(memory_id)
        if row["provenance"] == "player_canon" and row["memory_type"] == "canon":
            return
        if row["status"] in ("superseded", "merged", "deleted"):
            raise ValueError(f"Memory {memory_id} is {row['status']} and cannot be promoted")
        self._db.execute(
            "INSERT INTO canon_promotions (memory_id, old_provenance, evidence_kind, evidence_ref, promoted_at)"
            " VALUES (?,?,?,?,?)", (memory_id, row["provenance"], evidence_kind, evidence_ref, self._now()),
        )
        self._db.execute(
            "UPDATE memories SET provenance = 'player_canon', confidence = 1.0, memory_type = 'canon',"
            " status = 'active' WHERE memory_id = ?", (memory_id,),
        )
        # A confirmed guess that disputed a canon row replaces it: two active
        # canon rows contradicting each other would be the worse outcome.
        marker = re.search(r" \[disputes canon #(\d+)\]", row["details"] or "")
        if marker:
            older = int(marker.group(1))
            self._db.execute(
                "UPDATE memories SET status = 'superseded', superseded_by = ? WHERE memory_id = ? AND status = 'active'",
                (memory_id, older),
            )
            self._db.execute(
                "UPDATE memories SET details = NULLIF(REPLACE(details, ?, ''), '') WHERE memory_id = ?",
                (marker.group(0), memory_id),
            )
        self._db.commit()

    def promotions(self, memory_id: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM canon_promotions"
        args: tuple = ()
        if memory_id is not None:
            query += " WHERE memory_id = ?"
            args = (memory_id,)
        return [dict(r) for r in self._db.execute(query + " ORDER BY promoted_at", args)]

    def consolidate(self, session_id: str | None = None, *, window_days: float = MERGE_WINDOW_DAYS,
                    min_count: int = MERGE_MIN_COUNT) -> int:
        """Fold repeated identical observations into one summary row.

        Identical means same session, event_kind and summary text, at least
        ``min_count`` times within ``window_days``. Originals become
        status='merged' pointing at the summary; nothing is deleted. Chat
        turns are never merged (each is distinct content), nor are pinned or
        ai_inferred rows.
        """
        excluded = ",".join("?" for _ in UNMERGEABLE_EVENT_KINDS)
        rows = self._db.execute(
            "SELECT memory_id, session_id, event_kind, summary, provenance, importance, created_at_real"
            " FROM memories WHERE status = 'active' AND memory_type = 'episodic' AND pinned = 0"
            " AND provenance != 'ai_inferred' AND event_kind IS NOT NULL AND merged_into IS NULL"
            f" AND source_ids IS NULL AND event_kind NOT IN ({excluded})"
            " AND (? IS NULL OR session_id = ?) ORDER BY created_at_real",
            (*UNMERGEABLE_EVENT_KINDS, session_id, session_id),
        ).fetchall()
        groups: dict[tuple, list[sqlite3.Row]] = {}
        for row in rows:
            groups.setdefault((row["session_id"], row["event_kind"], row["summary"]), []).append(row)
        merged_groups = 0
        window = window_days * 86400.0
        for (scope, kind, summary), members in groups.items():
            start = 0
            while start < len(members):
                end = start
                while end + 1 < len(members) and members[end + 1]["created_at_real"] - members[start]["created_at_real"] <= window:
                    end += 1
                batch = members[start:end + 1]
                if len(batch) >= min_count:
                    self._merge(scope, kind, summary, batch)
                    merged_groups += 1
                start = end + 1
        return merged_groups

    def _merge(self, scope: str, kind: str, summary: str, batch: list[sqlite3.Row]) -> int:
        ids = [int(r["memory_id"]) for r in batch]
        provenance = "bridge_observed" if any(r["provenance"] == "bridge_observed" for r in batch) else batch[0]["provenance"]
        links: dict[int, str] = {}
        placeholders = ",".join("?" for _ in ids)
        for r in self._db.execute(f"SELECT entity_id, role FROM memory_entities WHERE memory_id IN ({placeholders})", ids):
            links.setdefault(int(r["entity_id"]), r["role"])
        try:
            new_id = self.add_memory(
                scope, "episodic", f"{summary} — {len(batch)}회 반복", provenance=provenance,
                importance=max(int(r["importance"]) for r in batch), event_kind=kind, entities=links,
                created_at=float(batch[-1]["created_at_real"]), source_ids=ids, commit=False,
            )
            self._db.execute(
                f"UPDATE memories SET status = 'merged', merged_into = ? WHERE memory_id IN ({placeholders})",
                (new_id, *ids),
            )
            self._db.commit()
        except Exception:
            self._db.rollback()
            raise
        return new_id

    def archive_stale(self, *, after_days: float = ARCHIVE_AFTER_DAYS,
                      max_importance: int = ARCHIVE_MAX_IMPORTANCE,
                      inference_ttl_days: float = INFERENCE_TTL_DAYS) -> int:
        """Retire what nobody recalled: low-importance old rows, and unpromoted
        inferences past their TTL (RFC-001 §9.5: guesses rot), disputed ones
        included. Pinned rows and canon are never archived."""
        now = self._now()
        cursor = self._db.execute(
            "UPDATE memories SET status = 'archived' WHERE status = 'active' AND pinned = 0"
            " AND memory_type != 'canon' AND recall_count = 0 AND importance <= ? AND created_at_real <= ?",
            (max_importance, now - after_days * 86400.0),
        )
        archived = cursor.rowcount
        cursor = self._db.execute(
            "UPDATE memories SET status = 'archived' WHERE status IN ('active','disputed') AND pinned = 0"
            " AND provenance = 'ai_inferred' AND created_at_real <= ?",
            (now - inference_ttl_days * 86400.0,),
        )
        archived += cursor.rowcount
        self._db.commit()
        return archived

    # ------------------------------------------------- batch cognition input

    def note_turn(self) -> int:
        """Count one completed turn toward the next maintenance run. The
        counter lives in the store, so short-lived processes add up."""
        self._db.execute(
            "INSERT INTO meta (key, value) VALUES ('turns_since_maintenance', '1')"
            " ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)",
        )
        self._db.commit()
        return self.turns_since_maintenance()

    def turns_since_maintenance(self) -> int:
        row = self._db.execute("SELECT value FROM meta WHERE key = 'turns_since_maintenance'").fetchone()
        try:
            return int(row["value"]) if row is not None else 0
        except (TypeError, ValueError):
            return 0

    def reset_turn_counter(self) -> None:
        self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('turns_since_maintenance', '0')")
        self._db.commit()

    def sessions(self) -> list[str]:
        """Session ids with recallable rows; the unscoped audit bucket is excluded."""
        rows = self._db.execute(
            "SELECT DISTINCT session_id FROM memories WHERE session_id != ? ORDER BY session_id",
            (UNSCOPED_SESSION,),
        ).fetchall()
        return [str(r["session_id"]) for r in rows]

    def recently_stored(self, session_id: str, summary: str, window_days: float = 7.0) -> bool:
        return self._has_recent_duplicate(session_id, clip(summary, MAX_LINE_CHARS), window_days * 86400.0)

    def active_note(self, session_id: str, kind: str, source_ids: list[int]) -> int | None:
        """The active reflection note already covering these sources, if any."""
        row = self._db.execute(
            "SELECT memory_id FROM memories WHERE session_id = ? AND event_kind = 'reflection'"
            " AND status = 'active' AND details = ? AND source_ids = ? LIMIT 1",
            (session_id, kind, json.dumps([int(i) for i in source_ids])),
        ).fetchone()
        return int(row["memory_id"]) if row is not None else None

    def missed_turns(self, session_id: str, since: float) -> list[dict[str, Any]]:
        """Turns where recall ran and nothing was selected (the retrieval gap
        Reflexion turns into a note). The first turns of an empty session do
        not count: there was nothing to fail to remember."""
        rows = self._db.execute(
            "SELECT rt.turn_id, rt.query, rt.candidates, rt.created_at FROM retrieval_turns rt"
            " WHERE rt.session_id = ? AND rt.selected = 0 AND rt.created_at >= ?"
            " AND EXISTS (SELECT 1 FROM memories m WHERE m.session_id = rt.session_id"
            "             AND m.created_at_real < rt.created_at)"
            " ORDER BY rt.created_at",
            (session_id, since),
        ).fetchall()
        return [dict(r) for r in rows]

    def rows_of(self, session_id: str, *, memory_type: str | None = None, status: str | None = None,
                event_kind: str | None = None, since: float | None = None) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM memories WHERE session_id = ?"
            " AND (? IS NULL OR memory_type = ?) AND (? IS NULL OR status = ?)"
            " AND (? IS NULL OR event_kind = ?) AND (? IS NULL OR created_at_real >= ?)"
            " ORDER BY created_at_real",
            (session_id, memory_type, memory_type, status, status, event_kind, event_kind, since, since),
        ).fetchall()
        return [dict(r) for r in rows]

    def session_sims(self, session_id: str) -> list[tuple[int, str, int]]:
        """(entity_id, name, linked memory count) for sims in a session, busiest first."""
        rows = self._db.execute(
            "SELECT e.entity_id, e.name, COUNT(*) AS n FROM memory_entities me"
            " JOIN memories m ON m.memory_id = me.memory_id"
            " JOIN entities e ON e.entity_id = me.entity_id"
            " WHERE m.session_id = ? AND e.kind = 'sim' AND m.status IN ('active','open')"
            " GROUP BY e.entity_id ORDER BY n DESC, e.entity_id",
            (session_id,),
        ).fetchall()
        return [(int(r["entity_id"]), str(r["name"]), int(r["n"])) for r in rows]

    def pair_evidence(self, session_id: str, a: int, b: int, *, since: float | None = None,
                      limit: int = 50) -> list[dict[str, Any]]:
        """Recallable rows linked to both sims, most important and recent first.
        Derived rows (summaries, reflections) are not evidence for themselves."""
        rows = self._db.execute(
            "SELECT m.* FROM memories m"
            " WHERE m.session_id = ? AND m.status IN ('active','open')"
            " AND m.memory_type != 'relationship_summary' AND COALESCE(m.event_kind, '') != 'reflection'"
            " AND (? IS NULL OR m.created_at_real >= ?)"
            " AND EXISTS (SELECT 1 FROM memory_entities x WHERE x.memory_id = m.memory_id AND x.entity_id = ?)"
            " AND EXISTS (SELECT 1 FROM memory_entities y WHERE y.memory_id = m.memory_id AND y.entity_id = ?)"
            " ORDER BY m.importance DESC, m.created_at_real DESC LIMIT ?",
            (session_id, since, since, a, b, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def relationship_summary(self, session_id: str, a: int, b: int) -> dict[str, Any] | None:
        """The active summary row for a sim pair, if one exists."""
        row = self._db.execute(
            "SELECT m.* FROM memories m"
            " WHERE m.session_id = ? AND m.memory_type = 'relationship_summary' AND m.status = 'active'"
            " AND EXISTS (SELECT 1 FROM memory_entities x WHERE x.memory_id = m.memory_id AND x.entity_id = ?)"
            " AND EXISTS (SELECT 1 FROM memory_entities y WHERE y.memory_id = m.memory_id AND y.entity_id = ?)"
            " ORDER BY m.created_at_real DESC LIMIT 1",
            (session_id, a, b),
        ).fetchone()
        return dict(row) if row is not None else None

    def archive(self, memory_id: int) -> None:
        """Retire one row by hand or by a batch rule (still recoverable)."""
        self._require(memory_id)
        self._db.execute("UPDATE memories SET status = 'archived' WHERE memory_id = ?", (memory_id,))
        self._db.commit()

    def supersede(self, old_id: int, new_id: int) -> None:
        """Retire ``old_id`` in favour of ``new_id`` (both must exist)."""
        self._require(old_id)
        self._require(new_id)
        self._db.execute(
            "UPDATE memories SET status = 'superseded', superseded_by = ? WHERE memory_id = ?", (new_id, old_id),
        )
        self._db.commit()

    # ----------------------------------------------------------------- admin

    def stats(self) -> dict[str, Any]:
        counts = {row["memory_type"]: int(row["n"]) for row in self._db.execute(
            "SELECT memory_type, COUNT(*) AS n FROM memories GROUP BY memory_type")}
        sessions = int(self._db.execute("SELECT COUNT(DISTINCT session_id) FROM memories").fetchone()[0])
        logged = int(self._db.execute("SELECT COUNT(*) FROM retrieval_log").fetchone()[0])
        return {"memories": counts, "sessions": sessions, "retrieval_log_rows": logged,
                "fts": self.fts_available, "path": str(self.path)}

    def explain(self, turn_id: str) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT l.memory_id, l.selected, l.score, l.breakdown, m.summary FROM retrieval_log l"
            " JOIN memories m ON m.memory_id = l.memory_id WHERE l.turn_id = ? ORDER BY l.score DESC",
            (turn_id,),
        ).fetchall()
        return [{"memory_id": int(r["memory_id"]), "selected": bool(r["selected"]), "score": r["score"],
                 "summary": r["summary"], **json.loads(r["breakdown"])} for r in rows]


def _reason(breakdown: dict[str, float], event_kind: str | None = None) -> str:
    parts: list[str] = []
    if breakdown["kw"] >= 0.5:
        parts.append("키워드 일치")
    if breakdown["ent"] >= 0.5:
        parts.append("등장인물 겹침")
    if breakdown["imp"] >= 0.7:
        parts.append("높은 중요도")
    if breakdown["rec"] >= 0.7:
        parts.append("최근 사건")
    if breakdown["open"] >= 1.0:
        parts.append("되새길 교훈" if event_kind == "reflection" else "미완료 약속")
    if breakdown["pin"] > 0:
        parts.append("고정됨")
    return ", ".join(parts) or "약한 관련성"


def _polarity_of(token: str) -> tuple[int, str] | None:
    """(pair index, side) when a token carries one of the known polarity forms."""
    for index, (positive, negative, mode) in enumerate(_POLARITY_PAIRS):
        if mode == "prefix":
            if token.startswith(positive):
                return (index, "a")
            if token.startswith(negative):
                return (index, "b")
        else:
            if token in positive:
                return (index, "a")
            if token in negative:
                return (index, "b")
    return None


def _polarities(tokens: list[str]) -> set[tuple[int, str]]:
    """Polarity marks in token order; a negation token flips the last one."""
    found: list[tuple[int, str]] = []
    for token in tokens:
        if token.startswith(_NEGATION_PREFIXES) and found:
            pair, side = found.pop()
            found.append((pair, "b" if side == "a" else "a"))
            continue
        mark = _polarity_of(token)
        if mark is not None:
            found.append(mark)
    return set(found)


def _two_sided(marks: set[tuple[int, str]]) -> bool:
    return any((pair, "a") in marks and (pair, "b") in marks for pair, _ in marks)
