# PlumbMate AI

**An LLM companion that plays The Sims 4 beside you, not over you.**

PlumbMate is a local-first bridge between The Sims 4 and a language-model
companion. You pick your Sim and a partner Sim; the partner talks with you in
Korean and proposes what it would like to do next. The model only ever sees a
small, schema-defined snapshot of the game, and its output is treated as an
untrusted *suggestion* that must pass validation before anything is shown.

Status: working alpha. Chat, automatic reply delivery, role selection and the
in-game socket bridge were verified live on macOS (TS4 1.127.41.1230) with a
mock provider. Executing gameplay actions is deliberately **not** enabled yet.

## Why I built it

Most AI-character projects either chat without touching the real game state,
or take over the controls entirely. The first feels disconnected; the second
removes the player's agency and puts their save data at risk. I wanted a
companion that **explains what it wants to do and asks first**, the way a human
second player would.

Building it turned into a small case study in *how to connect a model to an
environment it should not fully control*: what it may see, what it may
propose, what it may spend, and how to make every one of those boundaries
checkable rather than assumed.

## Design decisions

**Least-privilege input.** The model never receives screenshots, save files or
arbitrary files. A single versioned JSON schema defines every field that can
leave the machine; nothing outside it reaches a provider. Context is capped at
12,000 UTF-8 bytes and trimmed by whole optional items, never by cutting the
user's message or the role identities.

**Model output is untrusted.** Responses are validated against the schema
*after* local transforms. The model cannot override request IDs or introduce
executable commands; even a syntactically valid action is only a suggestion.
Game control is an allow-list, not mouse/keyboard access.

**Provenance, not self-promotion.** Stored statements are labelled as
game-observed, player-confirmed canon, or model inference. Model inference
cannot promote itself to canon; promotion, correction and deletion need a
deterministic rule or an explicit player action. Feelings are directional
edges (A feeling reassured by B does not imply B feels the same). Persistent
memory restoration stays off until save-scope receipts exist, because
restoring memories into the wrong save is worse than forgetting.

**Fail closed on money.** Paid calls require an explicit, unexpired price and
limit policy (default caps are zero). Spending is reserved *before* the HTTP
call and recorded before the answer is parsed. An uncertain charge keeps its
reservation and blocks further paid calls, even across restarts, until an
operator reconciles it. Uncertainty never triggers an automatic retry.

**Honest labelling.** Mock replies say they are connection tests, not AI play.
Failures appear as system notices, never as in-character dialogue. Late or
superseded replies are hidden rather than shown out of context.

**Game safety.** Game code runs on the game's embedded Python 3.7, does no file
polling, model calls or blocking work on the tick path, and talks to the
companion over a loopback socket authenticated per connection with an HMAC
challenge (the token itself is never sent). API keys never enter the Mods folder.

## Architecture

```text
The Sims 4 (script mod, Python 3.7)          Companion app (Python 3.11+)
  role picker / chat composer                  schema validation
  small state snapshot  ── loopback socket ──▶ context budget (12 KB)
  reply notification   ◀── + file bridge ───── provider worker (mock / OpenAI)
                                               spending ledger (SQLite)
```

```text
companion/   local provider bridge, budget/ledger, CLI (Python 3.11+)
game_mod/    The Sims 4 script mod (Python 3.7 compatible)
protocol/    versioned JSON schemas shared by both sides (generated, CI-checked)
docs/        architecture, design reviews, live-test evidence (mostly Korean)
tests/       410 offline tests; no game or API key required
```

## Tech stack

Python 3.11+ (companion), Python 3.7 bytecode built against the game's own
embedded runtime (mod), JSON Schema, SQLite, loopback sockets with HMAC auth,
OpenAI Responses API (opt-in), GitHub Actions on Ubuntu and macOS.

## Run it without the game

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e .
python -m plumbmate.cli init --bridge .plumbmate
python -m plumbmate.cli demo --bridge .plumbmate
python -m plumbmate.cli serve-once --bridge .plumbmate --provider mock
python -m plumbmate.cli show-latest --bridge .plumbmate

# tests
python scripts/export_contracts.py --check
python -m unittest discover -s tests
```

The mock provider needs no account, network or API key. Using a paid provider
requires `OPENAI_API_KEY` plus a billing policy; see
`python -m plumbmate.cli budget configure --help`.

## What is not done

- Executing approved gameplay actions (approval UI and action adapter).
- A console-free entry point verified in-game (alpha.9 is built, not installed).
- Billing has only been tested with synthetic usage, not a real paid call.
- Retrieval-based long-term memory on the main line. A memory slice
  (SQLite store, rule-based recall, merge/archive/dispute/canon promotion,
  relationship summaries) lives on the
  [`claude/memory-slice`](https://github.com/skadmlwls9455-pixel/plumbmate-ai/tree/claude/memory-slice) branch and has not
  been merged, because the game-side code diverged and cannot be re-verified
  without an in-game test pass.

## More detail

- [Development log](docs/DEVELOPMENT_LOG.md): alpha-by-alpha notes and what
  was verified live
- [Architecture and invariants](docs/ARCHITECTURE.md)
- [Design review decisions](docs/DESIGN_REVIEW.md)
- [Master design (Korean)](docs/MASTER_DESIGN_KO.md)

## How this was built

I developed this with AI coding assistants as pair programmers. I set the
product direction and safety constraints, reviewed the changes, and ran the
in-game verification on my own machine.

## License

See [LICENSE](LICENSE). This is a fan project and is not affiliated with or
endorsed by Electronic Arts or Maxis.
