# Real-Game Verification Resume Point — 2026-09-10

_English translation of LIVE_CHECKPOINT_2026-09-10_KO.md. If the two differ, the Korean original is authoritative._

## What was actually confirmed this time

- Connected to the game 1.127.41.1230 window and loaded the QA save.
- The `PlumbMate QA 2026-09-09` test save is a newly created slot, `Slot_00000002.save`.
- In the QA save, played the **BFF household** (3 Sims) in Willow Creek and paused.
  This entry point was saved to the QA slot. No existing user household was merged and no new Sims were created.
- Authenticated loopback diagnostics succeeded. The companion app is `mock`, per-run limit 8, no paid API calls.

## alpha.5 real-game confirmation results

- After the user opened the console, `plumbmate.status` confirmed alpha.5 / tick hook installed / autostart bound.
- In the two-step selection window of `plumbmate.setup`, player **Summer Holiday** and AI **Travis Scott** were each selected. The role result notification was confirmed.
- The currently selected Sim is Travis, but the sending role stays Summer.
- Confirmed the recipient display in the default `plumbmate.chat` window and that sending is disabled on empty input.
- After switching Korean/English, composed **안녕** ("Hi") with physical key input, and pasted and sent the sentence **안녕, 오늘 같이 산책할까?** ("Hi, shall we go for a walk together today?").
- **Before** running `plumbmate.read`, confirmed a mock answer containing the above sentence on the notification wall. The game was paused.
- Then ran `plumbmate.read`: output **이미 표시한 답변이에요** ("This answer was already shown"). No new answer notification appeared.
- Opened an empty input box again and cancelled: no new request/processing status file.

The user told us that console shortcuts don't work when the Korean input source is active.
In CUA, short shortcuts are still not stable, so it is not concluded that switching Korean/English alone
solves all key automation problems. For paste, even when the tool returned a timeout,
the text was actually entered into the field, so the screen was always rechecked before the next action.

The map lot was not selected by CUA's ordinary click but was selected by a drag at the same position.
UI button clicks work. This is an observation and does not mean the cause in the engine's input timing
has been determined.

## Caution about save data

Existing manual saves other than the new QA slot were compared and found identical to the backups.
However, during neighborhood travel/play, `Slot_ffffffff.save` and `.ver0`–`.ver4` were
updated. Therefore it must not be stated that "the original Saves as a whole were not changed."
The autosave before the change and all previous manual saves are kept in the git-excluded folder
`dist/private-playtest-backups/2026-09-09/saves/`.
Autosaves were not arbitrarily restored or deleted.

## alpha.6 changes and automated checks

- Changed the input box's old message requiring `plumbmate.read` to an automatic notification message.
- Reduced send-acceptance notifications and separated technical status from character dialogue.
- On 180-second wait expiry, a system message is shown once, only for the same request/role/scene.
  No message is left for a new household, role, or request, or for an already-displayed response.
  The expiry message also uses only UI on the game thread, with no file reads/model calls/automatic resends.
  Called after completion so that UI/console exceptions or callback reentry do not cause repeated notifications.
- Removed from the mock the phrase that agreed "좋아" ("Okay") to whatever was said, and the fixed date suggestion.
  It states that it is a receipt confirmation and does not generate memory patches either. It does not pretend to be an improvement in actual model quality.
- All **336 tests passed** (15.000 seconds), schema export and diff checks passed.
- Built 9 production modules successfully with the installed game's Python 3.7.

```text
dist/PlumbMate-alpha6.ts4script
SHA-256: 7eaa3113098d9c7514e46022c31c8e94888d55ea8fc35e9514f3b7489c848e62
```

After **Quit without saving** in the game UI, confirmed that the process was gone.
Without force-quitting, only the single `PlumbMate.ts4script` file was backed up and replaced, and the installed hash was
confirmed to match the alpha.6 build above. The backup is at the git-excluded path
`dist/install-backups/2026-09-10-alpha6/PlumbMate.ts4script`.
The existing mock companion app was terminated with SIGINT, then one new mock process was started.
`doctor --connect` succeeded, `api_calls: 0`.

The user asked us to first review the implementation burden of an emotion note/bubble UI, so the game
was not relaunched. **alpha.6's changed wording/expiry notification have completed automated checks
but have not been re-verified on the actual game screen.** Emotion/bubble features were not added to the install.

## Product work still remaining

### 2026-09-11 resume state

The alpha.7 portrait fix is installed in the actual Mods, and the build/install hash is
`058551e8f2dd0c95d7205c54096e740a3e4b46f2bb72444273ceec9591300b4d`.
The previous alpha.6 was backed up to `dist/install-backups/2026-09-10-alpha7/`.
Separately from the default notification's owner, the validated partner is passed as `icon_override`.
Character portraits are not automatically attached to system messages.

The game screen connection recovered via a path-specified reconnect after timing out the first two times.
The BFF QA household was loaded and is paused. However, CUA's console shortcut
did not open even with Korean/English switching, explicit modifier keys, or Mac accessibility combinations. Commands were not typed into an invisible
console. **There is not yet evidence that the portrait was actually displayed.**
If the user opens the console, setup/chat verification can resume immediately.
The companion app's authenticated loopback diagnostics succeeded, and there were no paid API calls.

The selective memory policy was made concrete in the [2026-09-11 decision proposal](SELECTIVE_MEMORY_2026-09-11.md).
Game observation/permanent storage was not enabled and existing records were not deleted.

### 2026-09-11 after the user opened the console — portrait verification complete

- `plumbmate.status` confirmed actual running alpha.7 / socket available / hook installed /
  autostart bound.
- In the role selection UI, designated player Summer and AI Travis.
- Opened `plumbmate.chat` **with the selected Sim changed to Summer**.
  The recipient stays Travis, and the new automatic receipt message is also visible.
- Pasted `오늘은 같이 쉬면서 이야기할까?` ("Shall we relax and talk together today?") in Korean and explicitly sent it.
- Before running a manual `read`, confirmed on the notification wall **a mock receipt confirmation with Travis's face, name, and the same message**. The game was paused.
- Afterwards, `plumbmate.read` was blocked with **이미 표시한 답변이에요** ("This answer was already shown"). No duplicate portrait notification.
- The distinction between new role/acceptance messages and character answers is kept. The mock states in its body that it is a connection
  check, not an actual AI utterance. No paid API calls, actual action execution, or save commands.

alpha.6's changed input/acceptance wording was confirmed in the alpha.7 check above.
The 180-second expiry notification and all UI exception cases have completed only automated checks; real-game confirmation is separate.
This is a success for this one household/one game build and does not mean Windows, other life states/ages, CAS changes,
or lot transitions have been verified.

1. An entry point that opens role setup/conversation without the console. Confirm the actual EA menu structure and connect it in a small way.
2. User-selected persona/speaking style and evaluation of **actual model** RP quality. Mock tests do not prove this.
3. Actual provider verification through an agreed cost policy. No paid API was used this time.
4. Memory/profile restoration connected to save lineage. Long-term memory is currently disabled for safety, and it is not a structure where memory appears just by changing the model.
5. Expand actual UI verification for long Korean/emoji input, lot transitions, and connection interruptions.

Generalization principle: QA Sim names are only test data and are not hard-coded.
No main merge/public release. Personal saves or chat transcript files are not uploaded.
