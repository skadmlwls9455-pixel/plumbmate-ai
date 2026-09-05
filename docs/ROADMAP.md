# Roadmap

## Milestone 0 — completed in this prototype

- Versioned filesystem protocol
- Offline deterministic demo
- Bounded OpenAI Responses adapter
- Python 3.7-compatible TS4 command source
- Game-runtime bytecode compiler and `.ts4script` build
- Unit tests for validation, state deltas, and bridge processing
- Script discovery/import smoke test on game patch `1.127.41.1230`

## Milestone 1 — safe in-game smoke test

1. [x] Install only `PlumbMate.ts4script` into a dedicated one-level-deep test
   folder while the game is closed.
2. [x] Confirm `PlumbMate` appears in the game's Script Mods list without a new
   `lastException.txt` or crash report.
3. [ ] Back up one throwaway save and load a new test household.
4. [ ] Verify `plumbmate.status`, `plumbmate.chat`, and `plumbmate.read`.
5. [ ] Inspect `lastException.txt` and remove the prototype immediately on error.
6. [ ] Confirm the request contains only the documented fields.

No action execution is enabled in this milestone.

## Milestone 2 — player-approved second-Sim control

- Add a stable companion-Sim assignment flow.
- Resolve interactions from an audited catalogue of tuning IDs.
- Offer three buttons: approve once, suggest another, and cancel.
- Queue one social/self-care action only after approval.
- Add cooldowns, stale-target checks, save/zone transition guards, and an
  emergency global off switch.

## Milestone 3 — companion experience

- Korean in-game notification UI and local chat window
- Relationship memory and date-plan templates
- Event-driven reactions to mood, milestones, and relationship changes
- OpenAI, Gemini, and local-model provider adapters behind the same interface
- Per-session cost cap and a visible token/price meter

## Milestone 4 — portfolio release

- Public demo video and architecture case study
- Threat model, privacy disclosure, and reproducible release checksums
- Signed/notarized companion builds for macOS and Windows
- Compatibility matrix for current game patches
- Automated tests against recorded, non-personal protocol fixtures
