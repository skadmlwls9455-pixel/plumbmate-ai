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
    ) -> int:
        if memory_type not in MEMORY_TYPES:
            raise ValueError("Unknown memory type: " + memory_type)
        if provenance not in PROVENANCES:
            raise ValueError("Unknown provenance: " + provenance)
        summary = clip(summary, MAX_LINE_CHARS)
        if not summary:
            raise ValueError("A memory needs a summary")
        keywords = tokenize(summary + " " + (details or ""))
        cursor = self._db.execute(
            "INSERT INTO memories (session_id, memory_type, provenance, confidence, importance,"
            " status, pinned, event_kind, summary, details, keywords, trigger_keywords, created_at_real)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (session_id, memory_type, provenance, float(confidence), int(max(1, min(10, importance))),
             status, 1 if pinned else 0, event_kind, summary, details, " ".join(keywords),
             json.dumps(trigger_keywords, ensure_ascii=False) if trigger_keywords else None,
             self._now() if created_at is None else float(created_at)),
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
        self._db.commit()
        return memory_id

    def remember_turn_request(self, request: dict[str, Any]) -> list[int]:
        """Store what the player said (an observed utterance) and any events
        the adapter reported with the turn. Returns the new memory ids."""
        session_id = str(request.get("session_id") or UNSCOPED_SESSION)
        entities = self._request_entities(request)
        actor_name = clip(str((request.get("actor") or {}).get("name", "플레이어")), 64)
        created: list[int] = []
        message = clip(str(request.get("player_message", "")), 200)
        if message:
            created.append(self.add_memory(
                session_id, "episodic", f"{actor_name}: {message}",
                provenance="bridge_observed", importance=IMPORTANCE_BY_EVENT_KIND["chat_turn"],
                event_kind="chat_turn", entities=entities,
            ))
        for event in request.get("recent_events") or []:
            text = clip(str(event), MAX_LINE_CHARS)
            if not text or self._has_recent_duplicate(session_id, text):
                continue
            created.append(self.add_memory(
                session_id, "episodic", text, provenance="game_observed", importance=3,
                event_kind="recent_event", entities=entities,
            ))
        return created

    def remember_turn_response(self, request: dict[str, Any], response: dict[str, Any]) -> int | None:
        session_id = str(request.get("session_id") or UNSCOPED_SESSION)
        message = clip(str(response.get("partner_message", "")), 200)
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
        payload = dict(payload or {})
        scope = str(session_id or payload.get("session_id") or UNSCOPED_SESSION)
        entities: dict[int, str] = {}
        if "sim_id" in payload:
            entities[self.ensure_entity("sim", str(payload["sim_id"]), str(payload.get("name", "")))] = "actor"
        if "target_sim_id" in payload:
            entities[self.ensure_entity("sim", str(payload["target_sim_id"]), str(payload.get("target_name", "")))] = "target"
        detail = json.dumps({k: v for k, v in payload.items() if k != "session_id"},
                            ensure_ascii=False, sort_keys=True)
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
        tokens = tokenize(query_text)
        present = self._request_entities(request)
        scored = self._score_candidates(session_id, tokens, present)
        selected = self._select(scored)
        if log:
            self._log(str(request.get("request_id") or "unknown"), scored, selected)
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
        scored: list[Recalled] = []
        for row in rows:
            memory_id = int(row["memory_id"])
            kw = keyword.get(memory_id, 0.0)
            ent = self._entity_overlap(memory_id, present)
            imp = row["importance"] / 10.0
            rec = self._recency(row, now)
            open_score = 0.0
            if row["memory_type"] == "promise" and row["status"] == "open":
                open_score = 1.0
                triggers = json.loads(row["trigger_keywords"]) if row["trigger_keywords"] else []
                if any(str(trigger).lower() in tokens for trigger in triggers):
                    open_score = 1.5
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
                round(score, 4), breakdown, _reason(breakdown),
            ))
        scored.sort(key=lambda item: (-item.score, item.memory_id))
        return [item for item in scored if item.score >= floor]

    def _entity_overlap(self, memory_id: int, present: dict[int, str]) -> float:
        rows = self._db.execute(
            "SELECT entity_id, role FROM memory_entities WHERE memory_id = ?", (memory_id,),
        ).fetchall()
        if not rows or not present:
            return 0.0
        total = sum(ROLE_WEIGHTS.get(row["role"], 0.4) for row in rows)
        matched = sum(ROLE_WEIGHTS.get(row["role"], 0.4) for row in rows if int(row["entity_id"]) in present)
        return matched / total if total else 0.0

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


def _reason(breakdown: dict[str, float]) -> str:
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
        parts.append("미완료 약속")
    if breakdown["pin"] > 0:
        parts.append("고정됨")
    return ", ".join(parts) or "약한 관련성"
