# 2026-09-09 Korean Input Box Checkpoint

_English translation of CHECKPOINT_2026-09-09_CHAT_KO.md. If the two differ, the Korean original is authoritative._

## Scope

A small input UI was added on top of the role contract of baseline commit `7d5b581`. The user
had finished the power settings and asked us to focus on coding. Game launch/installation/household
changes/saving/paid API calls were not done in this bundle.

## Implementation

- Running `plumbmate.chat` with no arguments requests a native input box showing the name of the
  designated AI partner. The console `<message>` method is also kept.
- `chat_input.py` handles only the pure input/draft policy, and `chat_ui.py` handles only the native window and status
  notifications. Actual sending is consolidated into one path, `commands._submit_chat`.
  The UI does not bypass role checks, duplicate checks for existing requests, or the companion app's cost limits.
- Opening/cancelling the window does not create a request file. Only the accept response of the send button is handled;
  duplicate callbacks, callbacks superseded by a new window, and role/location changes are not sent.
- Korean composing characters, spaces, line breaks, and emoji are not arbitrarily normalized or truncated to a prefix
  before sending. Only leading and trailing whitespace is removed. More than 1000 Unicode code points,
  empty input, wrong types, lone surrogates, and disallowed control characters are rejected.
- The native default maximum length in the install was 20. A dedicated field and length
  object for the conversation window are newly created so as not to modify the default tuning/other mods' singletons.
  The GUI-side length is 2000, taking UTF-16 counting into account, and the submit check is 1000 code points.
  How the native widget counts this limit or handles large pastes has not yet been verified for real.
- At most one fixable draft of up to 2000 characters is kept in local memory. It is cleared on cancel, successful
  send, or reopening under a different role/scene. There is no watch loop that automatically opens the window after a role change,
  and it is not put into saves/long-term memory/telemetry.
- The state of having left a request file is not confused with an answer actually being prepared. The status notification
  title is `PlumbMate · 대화 상태` ("PlumbMate · Conversation status"). Communication status is not acted out as the Sim's emotions/thoughts.
  Even if a console/notification error occurs, it does not resend or raise chained exceptions into game callbacks.

## Verification

Final full regression **307 passed, 12.336 seconds**. Passed schema matching, game Python 3.7 syntax,
and the check for missing build modules including transitive dependencies. Whether the maximum input length
matches the companion app's canonical request schema is also regression-checked separately.
Schema export and `git diff --check` also passed. Remote CI results are separate.

Static checks of the actual install:

- `ui/ui_dialog_generic.pyc` SHA-256
  `f528701fc4d2e0bda1d89e1710c84cf8c3c8adc2d10f882b2060d119787b35b4`
- `ui/ui_text_input.pyc` SHA-256
  `ed3db5b91e0ab5ecabe46e0fc7a5fa98d5902453377c1074a60e841531508586`
- Confirmed `UiDialogTextInputOkCancel`, `UiTextInput`, `_TextInputLengthFixed`,
  `text_input_responses`. Receiving requires `hasattr(text_inputs, name)`,
  and message creation requires `text_inputs.items()`, so both contracts are satisfied.
- `game_mod/verify_chat_ui_contract.py` selects only the 4 methods whose hashes match and
  runs them in a separate game Python 3.7 process with fake owner objects/message containers.
  It does not import the entire game module. It passed field key, initial value, and length limit passing and response receipt
  for 3 synthetic inputs: empty value / Korean, composing characters, and line breaks / 1000 emoji.
  The constructor/actual protobuf/rendering/IME are not proven by this check.

The game build `dist/PlumbMate-alpha4.ts4script` contains only the 8 production modules.
Development verification scripts and EA bytecode are not put into the distribution archive. Matching against the installed
magic `420d0d0a` was checked. Output SHA-256:
`b8a05cc6c8132183cd263d2f1bf72ec823f5e560fed2e70b18f86b193d222a78`.
This build is not yet in the actual Mods folder.

## Reproduction and next work

General regression: `.venv/bin/python -m unittest discover -s tests -q`.
Build: `python3 game_mod/build_game_mod.py --output dist/PlumbMate-alpha4.ts4script`.
The native method check can be reproduced with the isolated runner included in the repository. Run the following from the
repository root. Hashes for other game builds are not updated without review.

```sh
task_runtime=$(mktemp -d /private/tmp/plumbmate-ui-check.XXXXXX)
unzip -q '/Applications/EA Games/The Sims 4.app/Contents/Data/Simulation/Gameplay/base.zip' 'lib/*' -d "$task_runtime"
cc -std=c11 -O2 game_mod/inspect_with_game_python.c -o "$task_runtime/inspect-runner"
"$task_runtime/inspect-runner" \
  '/Applications/EA Games/The Sims 4.app/Contents/Frameworks/libpython370.dylib' \
  "$task_runtime/lib" "$PWD/game_mod/verify_chat_ui_contract.py"
```

The next vertical path is **automatic answer delivery**. Currently the user must run `plumbmate.read`.
Before implementation, the following boundaries are designed together.

1. A separate capability/version negotiation on top of the existing authenticated nonblocking socket.
2. Receive-permission check using request ID + role binding + scene + local send time.
3. The external app reads the response file and puts it in a bounded mailbox, and the game performs only limited
   socket work within the tick. Game tick file polling and calls to EA from background threads are prohibited.
4. Display only once; reject expired/reassigned/transitioned/oversized responses; no replay of old dialogue after a disconnect.
   Distinguish transport reconnection from model re-calls, and do not add automatic paid retries.
5. Use the same display gate as the manual read path. Do not hide the absence of the automation feature.

Before that, actual UI verification must confirm, with a permitted test household that has two Sims: role selection,
recipient display, direct IME input (including Enter during composition), cancel, long paste, notification display,
and rejection of sending from a window left open during a lot transition. The current game screen tool's
status query timed out, so this is not reported as real verification complete.
