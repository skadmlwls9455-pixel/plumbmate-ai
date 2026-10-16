# Verify Action Results; Select Memories Scene by Scene

_English translation of SELECTIVE_MEMORY_2026-09-11_KO.md. If the two differ, the Korean original is authoritative._

Status: pre-implementation policy decision proposal. Not currently connected to a game observation collector/permanent memory/summary model calls.
Existing conversation files are not deleted or converted into new long-term memory.

## Three different questions

1. **Did execution succeed?** Check the action's completion/cancellation/failure signals. Having said it or put it in
   the queue alone is not judged as execution complete. This check is separate from long-term retention.
2. **Is it worth remembering?** Select event candidates with local rules. No AI calls.
3. **In what sense is it remembered?** Within qualifying evidence, the main model proposes a short interpretation.
   The interpretation does not overwrite the original facts or create new facts.

Completion signals must also have their meaning verified by the relevant action adapter. A single interaction ending
does not prove the other party's acceptance, food delivery, and relationship recovery all at once.

## Proposed default recording scope

| Information | Where and how much | Long-term promotion |
|---|---|---|
| Start/completion/cancellation/route failure | Short size-limited diagnostic buffer | As a rule, no |
| Recent shared activities and need changes | In-session recent event buffer | Only the segments needed for candidate selection |
| Relationship changes, first experiences, important events | Scene candidates with evidence | After verifying outcome and context |
| Promises, unresolved questions | Separate pending items | Linked to fulfillment/cancellation/expiry; not duplicated each time |
| Repeated ordinary life | Summary of count, period, representative evidence | Habit candidate only when repetition is confirmed |
| Moments the player wants to keep | Player-designated items | Retained with priority, but the observation/claim distinction is kept |

Proposed initial experiment limits: per AI Sim, recent events **128 or 4 hours of game time, whichever limit is reached
first**, and scene candidates **16**. Adjust after evaluating actual importance and scene length.
Loads/branches/session changes isolate scope before time comparison. Game time that went backward is not
joined onto the same timeline. Before a stable save lineage, only session scope is allowed.
Segments dropped due to overload are marked as observation gaps and are not regarded as not having happened.

This does not mean keeping the entire diagnostic original indefinitely. Long-term events retain a minimal structured
evidence bundle alongside them for traceability, and the rest expires according to the stated retention policy.
The existing local chat archive does not yet have a retention UI and is not automatically deleted by this policy alone.

## Candidate eligibility and final selection are separate

Cases that can become candidates:

- Confirmed important world events (fire, birth, death, marriage, etc.). The actual event meaning per patch needs confirmation.
- Linking a confirmed relationship change to a completed interaction. Causation is not asserted from temporally close changes alone.
- Moments the player directly reacted to or designated. Player instructions are distinguished from the AI's voluntary choices.
- Completed outcomes related to known preferences/promises, or clear pending states.
- Repeated low-intensity daily routines. Retrying the same failure is not counted as repeated care/conflict.

Final selection considers, among candidates, importance, novelty, relevance to the current relationship/pending items, recent duplication,
and content diversity together. A relationship value +1 or a single hug is not unconditionally permanent memory.
When there is no higher-ranked event, a memory is not forcibly generated every hour/every scene.

So that the relationship does not always look unhappy from selecting only strongly negative events, calm shared activities,
small acts of care, and neutral first experiences are also included in the evaluation cases. Conversely, positive events that did not happen
are not added to achieve balance.

## Grouping scenes

Things are not grouped merely because they occurred in the same time window. Only sub-actions with a causal relationship are grouped,
based on the same scene scope, participants, and confirmed interaction/goal links.

Example (synthetic scenario):

- Original: B's low energy observed → A completes offering food to B → B's acceptance/eating confirmed.
- Fact summary: **A handed food to a tired B, and B ate it.**
- Interpretation candidate: **B may have felt cared for.**
- Prohibited: from the interaction alone, asserting **A cooked because A was worried about B** or **the two reconciled**.

If A only cooked and delivery was not confirmed, it is not grouped as 'cooked for someone.'
Independent events, such as C answering a phone call during caregiving, are handled separately and not swallowed during compression.

A scene is closed by the activity ending, a change of participants/location, sufficient inactivity, etc., but dangerous
states before it ends are reflected in the current context immediately. Survival control is not delayed while waiting for a memory summary.
An interrupted scene is not turned into a completed scene.

## Tokens and cost

- The entire original is not sent with every input. In addition to an **item cap** of current snapshot + short recent context + at most 3–6 related scenes,
  an overall input token/byte budget is also applied.
- Scene selection/deduplication is local. The structure is not made so that conversation is possible only after summarizing.
- Main model summarization only when a scene closes, there are candidates, and the budget allows. When exceeded,
  it is kept as short structured facts, and no automatic call retries/infinite queue are created.
- Summaries are not re-summarized every time. Versioned interpretations are made from the needed original evidence.
- The number of stored records and the context length of each request are separate. A structure where every request
  automatically gets longer because the day got longer is not allowed. This document does not claim measured savings rates.

## Pre-implementation acceptance cases

1. A meal step repeated 100 times does not create 100 long-term events or 100 model calls.
2. If a fire breaks out during a meal, it does not disappear into the ordinary meal bundle but becomes priority context.
3. A cancelled hug does not remain as 'hugged.' An observed refusal may be kept as a separate fact.
4. A→B and B→A are not swapped, and player instruction/survival control/AI voluntariness are separated.
5. The model's 'I was worried' is an utterance, not a verified fact about past intent.
6. Resending the same original/retrying a queue failure is not double-counted in repeated-habit statistics.
7. Time/scope does not get mixed under 3x speed, pause, travel, or save time regression.
8. A session with only calm daily life can be maintained, and forced conflicts/milestones are not generated.
9. The user's importance marking does not promote fictional content into a game-observed fact.
10. Even when token/retention caps are reached, pending promises and current dangerous states are not blindly dropped in oldest-first order.
