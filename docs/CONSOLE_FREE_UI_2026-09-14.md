# alpha.9 · Console-Free Entry Point Prototype

_English translation of CONSOLE_FREE_UI_2026-09-14_KO.md. If the two differ, the Korean original is authoritative._

## Status

2026-09-14: source implementation / free automated tests / game Python 3.7 build complete.
**Installation and in-game verification are on hold. The current install is alpha.8.**
The user asked us to stop using the screen and focus in order to study. After that, no CUA operation,
game exit/relaunch, Mods replacement, household selection, saving, or paid API calls were done.
An earlier attempt to open the game app ended in a timeout, so it is not recorded as a completed launch.

## Implementation actually added

- `control_ui.py`: Open PlumbMate from the wall → Open conversation / Select roles / Close.
- Finishing the two-step selection player Sim → AI Sim continues into the input box. No automatic sending.
- Native button callbacks have no console connection ID, so the actual `ClientManager.get_first_client()` path is used.
- The send success message does not create an additional wall notification. One recent acceptance message bound to that role/scene
  is shown in the menu, and the pending state is checked in the existing status window.
  This is confirmation of local request creation, not confirmation of remote processing completion or billing cancellation.
- AI answers, portraits, failure messages, and blocking of duplicate/late answers keep their existing paths.

## Structure and safeguards

- The UI-handling module is separated, and commands handle only wiring.
- The memory-only lifecycle check on the game thread runs at 5-second real-time intervals. No file reads,
  stat, worker creation, or API calls. It works even when there is no companion app.
- Holds only one launcher for the current lot/household and releases only its own window on transition/stop/reload.
  Previous windows are invalidated by epoch even after stop/start in the same household.
- show errors are attempted at most 3 times per continuous scope. UI failures are separated from the transport consecutive-failure
  counter and do not stop receipt of normal responses.
- Does not overwrite the game's global notification class or other mods' windows.
  Server registration is kept via the `respond=False` path of its own subclass.
- Close/cancel/unknown responses from the menu are not interpreted as role changes or sends.

## Evidence confirmed

- Full unittest **371 passed**, 15.634 seconds. Temporary localhost tests with no screen access or paid calls.
  In the default sandbox, localhost bind was blocked, so that failure was isolated and the tests were rerun within the permitted scope.
- `game_mod/verify_control_ui_contract.py`: confirmed response IDs, additional buttons, and listener wiring in hash-pinned game bytecode.
  Ran only on a fake object owning the actual `UiDialogService.dialog_respond` method
  to confirm repeated activation/close/exception cleanup.
- This is not a check that imported and displayed the actual UI classes. This distinction is kept.
- `dist/PlumbMate-alpha9.ts4script`: 10 root pyc, game magic `420d0d0a`.
- SHA-256: `a01e4b6f09c82c397111e4a7801aa1d1bbff87cb9fbf9d38fec558652b20a328`.
- Not copied to the install folder. No remote push/public release either.

## Required acceptance checks when the screen is available

1. Whether the native subclass TunableFactory is created at actual runtime.
2. Whether the launcher button is visible after entering the QA household, without a forced popup.
3. Whether the menu can be reopened by pressing the same button two or more times. Do not assume that keeping server registration
   guarantees reuse of the client button.
4. Without the console: select two roles → Korean input → mock answer with the actual name/portrait.
5. Whether the success message appears in the reentry menu/status window without filling a separate wall.
6. No resend on close, cancel, duplicate clicks, lot transition, stop/reload, or companion app shutdown.
7. Confirm notification delete/clear-all behavior and the user recovery path.

## Usability not yet complete

This is **not the phone's send-text menu**. The existing same-household/lot restriction is also kept.
Because it does not automatically reappear when the user deletes the notification, it cannot be called complete as a permanent click entry point.
Recovery is currently `plumbmate.ui` or reentering the lot.
After verifying actual button reuse, separate tuning/injection work for the phone/Sim menu will be decided.
