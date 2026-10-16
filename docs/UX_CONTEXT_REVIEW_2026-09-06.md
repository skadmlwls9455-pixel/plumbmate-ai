# Usability and Long-Term Context Review

_English translation of UX_CONTEXT_REVIEW_2026-09-06_KO.md. If the two differ, the Korean original is authoritative._

This is the review record of 2026-09-06. On 2026-09-07, the cost ledger, diagnostic CLI, ACK deadline, and keepalive were implemented locally, and gaps in role designation and UI were confirmed in the actual usage flow. For the latest state and resume order, the [checkpoint](CHECKPOINT_2026-09-07.md) takes precedence. "Current/next" below is as of the time of review.

## Conclusion

A successful log/socket round trip is not evidence that the player experience is complete. It is currently a developer console prototype. Long-term memory, Sim designation, automatic actions, and automatic response display must not be presented as complete.

## What is in the code now

| Concern | Actual implementation | Limits |
|---|---|---|
| Input safety | Field, type, and length checks from a single JSON Schema; extra fields rejected before the provider | Format validation does not replace verification of factuality/memory provenance |
| Context size | context JSON 12,000 UTF-8 bytes; actor/current utterance preserved, additional context reduced item by item | Not an exact token count; system prompt and output schema overhead are separate |
| Call count | All CLI services use the engine's default session budget of 20, uncertain failures also deducted | Reset on restart. Not a daily cost limit |
| Processing feedback | Per-request processing/completed/failed and fixed reason_code; failures and limits explained in game read | Not displayed automatically in the UI. processing can linger for a long time if the app crashes |
| Privacy | Only permitted input is sent; event body console output only with an explicit verbose option; no original text/keys in status files | Request/response archive remains locally. No retention/deletion UI yet |
| Long-term memory | Reuse of memories from unverified saves blocked; model memory_patch not stored as fact | Recall/preference/relationship growth features are not connected yet |

## Principles for keeping long-term play lightweight

The amount of stored data and the amount of model input are not treated as the same thing. Even as events grow in the future DB, only the current scene, a narrow recent buffer, and a few related memories are sent to the model. The full chat history is not continuously attached, and the LLM is not made to summarize the entire DB every turn.

The priority is ① validated save/branch scope ② actor/target and observation provenance ③ deterministic candidate retrieval for the current topic, counterpart, and location ④ candidate scoring/deduplication ⑤ fixed-budget packer. When a needed memory is cut outside the budget, "don't know/withhold" must be allowed, and the LLM must not be induced to fill in the cut information.

The current 12KB packer is a foundation that guarantees format and total size. It is not a long-term memory retriever that understands the semantic importance of items. The 200-turn synthetic mock check only confirms that there is no automatic accumulation of input history; it does not prove DB retrieval speed over months of data or RP quality.

## Next user experience improvement order

1. **Separate settings from status**: a diagnostic screen that separately shows the actual bridge path, game mod version, companion connection, provider settings, and request processing status. "Socket connected" is not displayed as "AI ready."
2. **One input → automatic response display**: replace the flow of alternately typing chat/read in the cheat console. Match each request ID, not just the last request, and block duplicate display/responses from a previous lot. Do not reintroduce file I/O in the game tick; design a separate bounded response channel.
3. **Make clear who you are talking to**: display the selected Sim and the AI partner Sim separately. Currently it is an active-Sim-based snapshot, not a completed multi-partner setup.
4. **Small vertical features**: one observed event → one memory with provenance → one appropriate recall. Then connect a single permitted action and completion observation. Do not start by multiplying empty classes named after RFCs.
5. **Actual usage/cost UI**: record the input/output/cached usage returned by the provider and distinguish it from per-model pricing information. Do not display estimates as if they were billed amounts. Reserve budget just before the API request and keep a persistent daily limit, also accounting for unconfirmed calls after process restarts/crashes.

## Risks that must be closed separately before distribution

- Persistent claim/idempotency for the case where two companion engines process the same request directory simultaneously and the case where the process dies during an API call. The current "no automatic retry" is a policy for failures handled by a single living engine.
- Isolation of pending responses across save switches/household switches, and connection to validated save receipts.
- Socket ACK deadline/keepalive/unsent outbox migration, endpoint change UX.
- Retention periods, local record deletion/export, and a disk usage cap. Even if the prompt is limited, the disk archive keeps growing.
- Exact per-model output limits/actual usage handling and prompt-cache key policy. Savings rates are not guaranteed merely because a caching request field exists.
- Separation of macOS/Ubuntu offline checks from actual Sims 4 checks. Windows installation/firewall/key storage require additional real-environment verification.
