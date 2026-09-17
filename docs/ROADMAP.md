# Roadmap — implementation status, 2026-09-17

This is an experimental dialogue bridge, not yet an autonomous second player.
The master design is a target architecture, not an implemented-feature list.
See [the current work plan](WORK_PLAN_2026-09-17_KO.md) for ordered acceptance gates.

## Implemented in the current development branch

- Versioned contracts, bounded context, isolated request/reply processing.
- Nonblocking authenticated loopback transport, reconnect and stop controls.
- Explicit player/partner roles, native Korean composer and notification replies.
- Portrait routing, duplicate/stale response suppression, pending/dismiss UI.
- OpenAI adapter, durable spending reservations and bounded external worker IO.
- Game Python 3.7 compiler, offline tests and native bytecode contract probes.
- Alpha.9 launcher/control menu **prototype**, not installed/live-accepted.
- Separate UI, role identity, game-scene adapter and current-conversation policy.

## Live verification already recorded

On the QA Mac / game 1.127.41.1230, alpha.8 and earlier passed the native role
picker, basic Korean input, designated partner portrait, automatic mock reply,
manual duplicate suppression and dismiss → reconnect → fresh-message flow.
These tests used a backed-up QA save, not paid providers. They do not certify
every UI path, platform, game patch or third-party-mod combination.
See [the recovery checkpoint](UX_RECOVERY_2026-09-12_KO.md).

## Next: console-free conversation

- Install a backed-up candidate only when the game is closed.
- Confirm launcher construction and repeated client-side button activation.
- Complete roles → compose → mock reply without console commands.
- Check cancellation, off-lot Sims, transitions, duplicate clicks and notification deletion.
- Phone/Sim-menu tuning is separate work; the launcher is not that integration.

## Next: memory integration, not duplicate implementation

`claude/memory-slice` contains SQLite memory, bounded rule-based recall,
maintenance/reflection and a needs adapter. It is **not merged into this branch
or installed**. Review provenance, recording consent, migrations and isolation;
the current per-turn session ID cannot support cross-turn recall unchanged.
Start with explicitly scoped same-session conversation continuity. Save-spanning
recall remains blocked until a verified save-lineage integration exists.

## Next: observed events and one safe action

- Add one verified event adapter with actor/target provenance and bounded retention.
- Add one allow-listed, player-approved action with completion/cancellation evidence.
- Add stale-target, queue ownership, cooldown and emergency-off safeguards.
- Only then expand to autonomous intents, survival arbitration and model routing.

## Later: companion experience and distribution

- Persona editing, broader provider support, relationship growth and milestones.
- Ordinary-player installer/settings, visible spending controls and key storage UX.
- Paid-provider final acceptance within a separately approved test budget.
- Privacy/export/delete controls, retention and compatibility matrix.
- Signed/notarized desktop distribution, demo and portfolio case study.

Publishing development commits does not mean release approval or game certification.
