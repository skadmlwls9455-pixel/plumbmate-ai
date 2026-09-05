# Design review decisions

## Memory retrieval without mandatory embeddings

The default retriever must work offline without a vector model. Candidate
memories will first be filtered by participants and hard constraints, then
ranked with an inspectable weighted score over location, event type, unresolved
status, salience, recency, and lexical/alias overlap. Embeddings remain an
optional reranker, never a required dependency. The UI or debug output should
expose the score components that caused each memory to be selected.

This intentionally trades some semantic recall for low setup cost, privacy, and
deterministic behavior. The limitation and the optional embedding tradeoff must
be stated in public documentation.

## Directional relationship state

Feelings are edges, not a shared property of a pair. Every affective memory must
identify `source_sim_id` and `target_sim_id`, alongside emotion, intensity,
confidence, provenance, and supporting event IDs. Aiden feeling reassured by
Kate must not imply that Kate feels the same way.

## Provenance and canon

Stored statements must distinguish game-observed facts, player-confirmed canon,
and model inference. Model inference cannot promote itself to canon. Promotion,
correction, pinning, and deletion require a deterministic rule or explicit
player action.

## Patch and ABI resilience

End users should install a prebuilt release, not compile Python. The maintainer
build must discover the installed game's Python runtime, compile with that exact
runtime, compare the produced bytecode magic number with `core.zip`, and publish
a patch compatibility matrix plus checksums. The current macOS prototype proves
the approach for the game's custom 16-bit Python `wchar` ABI; runtime-version
discovery and release automation remain required before public distribution.
