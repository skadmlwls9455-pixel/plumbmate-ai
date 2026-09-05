# Architecture and safety model

## Components

```text
The Sims 4
  └─ PlumbMate.ts4script
       └─ small JSON request files
            ↓
       local bridge directory
            ↓
  companion process ── provider adapter ── optional external model API
            ↓
       validated JSON response
            ↓
  player review → allow-listed game action (future milestone)
```

The game process never makes an internet request. The companion is the only
network-capable component, so it can keep provider credentials outside the game
and make data transmission visible and optional.

## Trust boundaries

1. **Game state is private by default.** Only fields defined by the public
   request schema can cross the bridge.
2. **Model output is untrusted.** It must pass the response schema and action
   allow-list before the game sees it.
3. **No arbitrary commands.** A model cannot provide Python, console commands,
   file paths, tuning identifiers, or mouse/keyboard instructions for execution.
4. **Approval before impact.** The intended product asks the player before a
   social or object interaction is queued.
5. **Fail closed.** Missing identifiers, malformed JSON, unknown actions, stale
   responses, and unavailable providers all become `suggest_only` or an error.

## Token and cost strategy

- Trigger on a player message or meaningful event, never on every simulation
  tick or rendered frame.
- Send structured state, not screenshots or raw save data.
- Hash the last state and send `state_delta` only when fields change.
- Keep a local fact memory capped at a small number of relationship-relevant
  facts; summarize at milestones rather than every turn.
- Put stable persona and safety instructions first so provider prompt caching can
  reuse the prefix.
- Use short structured output and a hard `max_output_tokens` limit.
- Use a small/low-latency model for ordinary turns; escalate only for rare story
  planning turns. Model routing is a companion setting, not model-generated.
- Debounce duplicate events and cache equivalent low-stakes replies locally.
- Record provider-reported usage without recording secrets or full private
  prompts in analytics.

## Privacy disclosure for a public release

The installer and README should show the exact request schema, provider,
retention choice, and example payload before asking for a key. Local/mock mode
must remain usable without an account. Logs should redact keys and offer a clear
delete-history control.

