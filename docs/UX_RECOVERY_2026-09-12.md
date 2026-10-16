# alpha.8 Usability and Failure Recovery Checkpoint

_English translation of UX_RECOVERY_2026-09-12_KO.md. If the two differ, the Korean original is authoritative._

Paid testing is on hold until the final check, at the user's request. No payment, key setup,
actual model calls, budget increase, or public release was done.

## Implementation

- When `plumbmate.chat` is resumed and a request has already been sent, a status window is shown instead of a new input box.
  It distinguishes no processing confirmation/processing, elapsed real time, and whether automatic receipt is in effect.
  It does not make up an expected completion time or character emotions.
- The status window's **닫고 기다리기** ("Close and wait")/Escape does not cancel the wait.
  Only **기다리기 끝내기** ("Stop waiting") blocks subsequent notifications and manual reads for that request.
  The console alternative is `plumbmate.dismiss`.
- The request, role, and location are re-validated just before ending. Even if a stale status window is pressed after
  the answer has already arrived or another request has been sent, it does not touch the new conversation.
- Cancelling a sent request/charge and ending the on-screen wait are explained separately.
  No resending, record deletion, or release of the billing reservation is done.
- Even if the automatic notification was missed, reopening the conversation window reads the prepared answer for exactly that request.
  Even corrupted status/response records can open the wait-ending UI.
- If there are no roles, it links to role setup instead of an empty chat. The limitation of a one-Sim household is
  communicated by a system notification as well as the console, and Sims are not created automatically.
- Strengthened the message that billing blocks are not lifted by resending/restarting.
- Only external HTTP runs in a separate spawned process. Fixed 1 MiB shared result buffer;
  30-second limit for input token checks / 60-second limit for generation, with up to 2 seconds each added for shutdown cleanup.
  The parent companion app owns the billing ledger and continues to hold unconfirmed amounts.
  No file polling, processes, threads, or API calls were added to the game thread.

## Verification evidence

- 356 free automated checks passed. 15 new compared with the previous 341.
  The full suite was run twice and passed in 15.540 seconds / 15.412 seconds respectively.
- Deliberately delayed an actual spawned child process and confirmed timeout → child cleanup →
  success of the next local mock request. No network/paid model was used.
- Checked duplicate/stale windows, role/location changes, new sends after ending the wait, retrieval of prepared responses,
  corrupted records, and blocking of duplicate execution after UI failure.
- Confirmed offline `UiDialogOkCancel`, `ButtonType`, and the
  OK/CANCEL response paths in the installed game's `ui/ui_dialog.pyc`. SHA-256:
  `c0c98ecebbb9f0240f25ab7ed2efed07cb0b6f75be2e41d69fe44f8ba2280849`.
  The new status window checks the exact OK response identifier instead of guessing `accepted`.
- Build/magic check of 9 production modules with game Python 3.7 succeeded.
  With no game process running, only the single PlumbMate file in the actual Mods was replaced.
  New install SHA-256: `44999c9243425a261788467bfc20af1bf5e215187476384dd63755a557f2f465`.
  The previous alpha.7 was backed up to `dist/install-backups/2026-09-12-alpha8/PlumbMate.ts4script`
  and its SHA-256 `058551e8f2dd0c95d7205c54096e740a3e4b46f2bb72444273ceec9591300b4d` was checked.
- This does not replace actual screen/Windows/actual API verification.
  The portrait success of the installed alpha.7 is not reused as verification of the new status window.

## Still remaining — do not treat as complete

1. Expand verification to long text/emoji/Windows/other game builds. The Mac real-game check of new Korean input → new mock response after
   ending the alpha.8 wait was completed as recorded below.
2. A console-free game menu entry point. Currently the `plumbmate.chat` entrance is still needed.
3. Ending the wait is a **screen policy**. A contract to cancel requests still queued on the server
   is not implemented, and cancellation/refund of remote calls already sent is not guaranteed either.
4. A recovery GUI per cost-block reason. Currently there are only messages + the companion app's `budget status`/
   explicit reconciliation tool. Unconfirmed costs must not be released automatically.
5. Actual model naturalness, persona settings, and long-term memory are separate implementation/evaluation stages.
6. A real-time upper bound is not guaranteed even for abnormal OS process creation/force-kill failures.
   There is a defense that does not start the next worker when shutdown fails.

## Free real-game check scenario (basic path completed 9/13)

- Send without starting the companion app → reopen the conversation → check status → close →
  reopen and end the wait → previous notification blocked → send a new conversation.
- Confirm a normal response with the mock companion app and regression-check portrait/roles/duplicates.
- Use a separate experimental bridge and QA save, and do not delete existing personal records.
  An app with actual API calls enabled is not used as a failure experiment target.

## 2026-09-12–13 actual screen confirmation

- While running, `plumbmate.status` confirmed alpha.8. BFF QA household,
  roles selected as player Summer / AI Travis. A bare chat opened the setup window first.
- On 9/12, after sending the Korean input `대기창 확인용 메시지예요.` ("This is a message for checking the waiting window."), reopened chat and
  confirmed that the status window title/elapsed seconds/not-connected message/two buttons were displayed without being cut off.
- When resumed after **닫고 기다리기** ("Close and wait"), the wait for the same request was kept.
- After **기다리기 끝내기** ("Stop waiting"), a system message appeared and `plumbmate.read` was
  blocked with `기다리기를 끝낸 답변은 표시하지 않아요` ("Answers you stopped waiting for are not shown").
- On resuming on 9/13, the roles had been reset, so they were designated again and a new mock pending request
  `cb9d66f9832b413abbc1a00bc0ffbb7c` was made to recheck ending the wait.
- The actual companion was rerun with `--provider mock --max-turns 8`.
  The previous request `b4d3edefa4be47dcb740fc35214cf18c` and the above request completed, but
  the old answers whose wait had been ended did not reappear as game notifications.
- The Escape input first closed the console, and the next input did not close the status window either.
  Therefore **it is not claimed that the UX of closing the status window with Escape has been verified.**
  The on-screen `닫고 기다리기` ("Close and wait") button works normally.
- While the CUA input focus slipped outside the console, game shortcuts fired, the travel map
  opened, and time advanced briefly. The travel was cancelled and the game was paused.
  No manual save was performed. It is not claimed that the game stayed paused throughout the entire screen verification;
  this phenomenon is further evidence that a console-free entry point is needed.
- Automation of the console reopen shortcut was blocked, so the user was asked only to open the console.
  In the resumed check below, the remaining new-message round trip was also completed.

### 9/13 after the user opened the console — new conversation recovery complete

- Confirmed `--provider mock --max-turns 8` in the run arguments of the existing mock process PID 39937.
  Without restarting the game/companion app, a new input box was opened with the existing roles.
- After confirming on screen that `이제 다시 이야기하자. 오늘 같이 쉬자!` ("Let's talk again now. Let's relax together today!") was entered as Korean intact,
  it was sent. New request ID `c355cb891b4c41069f16bac56368d268`.
- The companion completed the third mock request, and its status file is also completed.
- **Before** running a manual read, a mock response containing Travis's name/face and the new sentence was automatically
  displayed on the notification wall. Previously ended responses did not reappear.
- `plumbmate.read` was refused with `이미 표시한 답변이에요` ("This answer was already shown") and did not create a new notification.
- During this resumed check, the game was paused at Monday 11:21 and there were no save commands.
  No actual paid model calls/charges. The mock companion app was kept at 3/8 processed.
- What was confirmed is the **mock recovery path** of on-screen wait ending → reconnection → new conversation.
  Communication failures, remote cancellation, and billing verification with an actual paid provider are on hold until the final check.
