# 2026-09-08 Role Contract and Display Safeguards Checkpoint

_English translation of CHECKPOINT_2026-09-08_ROLES_KO.md. If the two differ, the Korean original is authoritative._

Follow-up changes: [2026-09-09 Korean input box checkpoint](CHECKPOINT_2026-09-09_CHAT.md).
What follows is history as of `7d5b581`; for the subsequent input UI implementation, follow the follow-up document.

## Scope and baseline

The cost/performance/diagnostics checkpoint was preserved in local commit `7f697a1` (261 tests).
This small change removes the incorrect assumption that "the currently selected Sim = the AI speaker."
No public release, paid calls, game saving/household changes, or replacement of the actual Mods was done.
Local source, build outputs, the actual installation, and what is reflected on the remote GitHub are distinguished.

## What went into code this time

| Boundary | Implementation | Remaining limits |
|---|---|---|
| Game roles | `role_binding.py`: explicitly designate two different SimInfo/IDs from the same household | Current lot runtime only; not a persistent setting/save identification |
| Selection screen | `role_setup_ui.py`: two-step native picker player → AI, defenses against cancel/stale windows/duplicate callbacks | Entry is via the `plumbmate.setup` console; not yet verified on the actual screen |
| Conversation contract | `dialogue.py`: rejects unassigned/same/outside-household/duplicate ID/actor mismatch | Up to 12 household members; only limited checking of the two Sims' current lot availability |
| Model input | Projects only the specified player/partner/message/recent_events; both role IDs preserved during packing | The AI partner's detailed needs/personality/memory are not yet collected |
| Display | `plumbmate.read` → native notification, console on failure; requests already displayed are not redisplayed | Automatic delivery/actual Korean input box not implemented |
| Late answers | Display blocked on reassignment, zone/household object replacement, Sim removal/re-creation, or after 180 real seconds have elapsed since sending | Not a feature to cancel/refund an API request already in progress |
| Sending | Additional sends blocked while an undisplayed request for the same roles is valid; failed state allows a new send | General request ownership across multiple windows/multiple companion apps is a follow-up task |

In a one-Sim household, roles are not arbitrarily created/duplicated; only guidance is shown. Changing
the selected Sim does not change the roles. If the partner is not on the current lot, the conversation is deferred.
This check is not complete lifecycle handling for rabbit holes/inactive households.

`actor` is now **the designated player**. `roles` holds the player/partner IDs and arbitrary
binding/scene tokens. The scene token is a runtime display fence, not evidence of save_scope or
save_commit. When object replacement is confirmed, it is safely reset.

`roles` in the request shape is an optional field in order to give older files a specific rejection
reason, but the engine and provider strictly require it before model execution. The protocol notation is
still the unreleased `0.1`. **The alpha.3 game and companion app must be used together.** An older
companion app rejects the new field, and the new companion app quarantines older requests without roles.

## Verification and evidence

- Checked that there is no provider call and no deduction of session attempts for requests with unassigned roles/the same Sim/outside the household/wrong actor/duplicate IDs/out-of-range values.
- Korean multi-word requests, changing the selected Sim, role reassignment within one household, lot transitions,
  duplicate chat/read, expired responses, and partner absence were checked with game API stubs.
- The selection window was checked only by reading the symbols/argument order of the bytecode of the actual install `1.127.41.1230`.
  Because the first argument of `UiSimPicker.__init__` is `sim_filter`, the owning Sim is passed as `owner=`.
  This is regression-checked with a stub that does not let positional-argument mistakes pass.
- Whether the notification/picker actually appears on screen, and whether direct IME Korean input works, have
  not yet been confirmed. The screen tool's status query timed out, so the game was not operated.
- Synthetic 500 turns: pack + actual model context projection median 0.380ms, p95 0.430ms,
  average 936→517 UTF-8 bytes. A single local measurement; not a token, full prompt, or charge reduction rate.
- Final full regression **281 passed, 11.925 seconds**. Schema export and `git diff --check` passed.
- Compiled 6 production modules with the real game's Python 3.7 and checked that they match the installed bytecode
  magic `420d0d0a`. `dist/PlumbMate-alpha3.ts4script` SHA-256:
  `0232ac8fcbfff868ba87c4e2dcb2dc1b544640de106d57de4bb84a48eb4f750b`.
  This output has not yet been installed into the actual Mods. It is also not a remote CI result.

Normal test answers are prefixed with `[연결 테스트·mock]` ("[connection test · mock]"). A local connection success is not presented as
a success of an actual AI partner or autonomous action. The existence of selection API symbols/build
success/stub tests do not substitute for actual on-screen verification.

## Next vertical work

1. After exiting the game, back up/replace only our own mod and run the mock companion from the same source.
   In a household with two Sims that the user permits, without saving: `plumbmate.setup` → role
   selection → Korean chat → one read/notification → change selected Sim → check that roles are kept.
   Do not arbitrarily add to the current one-Sim household.
2. Test the actual API/IME of the native text input box. Specify the recipient and place send/preparing/failure
   feedback near the conversation channel. Do not make a loading indicator above the Sim.
3. For automatic notifications, first design a bounded bidirectional socket response channel and request/scene fencing.
   Do not add file reading/polling in the game tick for convenience.
4. Do not enable persistent role/memory restoration before the save lifecycle is confirmed. Even after this stage is done,
   L0–L3/IC/autonomous action/relationship memory are not counted as implemented.
