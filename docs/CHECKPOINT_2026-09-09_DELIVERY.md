# 2026-09-09 Automatic Answer Receipt Checkpoint

_English translation of CHECKPOINT_2026-09-09_DELIVERY_KO.md. If the two differ, the Korean original is authoritative._

## Scope completed this time

alpha.5: the answer to a single explicitly sent chat is fetched over the existing authenticated loopback connection
and automatically delivered to the notification wall adapter. The `plumbmate.read` manual recovery path also
remains. Automatic action execution, long-term memory, and save-branch restoration were not added this time.

- `companion/turn_delivery.py`: after confirming binding/scene against the current request or an archived request with the same ID,
  projects only the answer. Memory patches, full history, and cost information are not
  sent over this channel. Failures convey only fixed system reason codes.
- `event_socket.py`: handles `turn_poll` after negotiating `turn_delivery_v1`.
  This read request does not fill the game event queue. Unsupported companion apps keep manual checking.
- `game_mod/reply_delivery.py`: handles single-request watching, real-time intervals, expiry,
  and role/scene change checks. No EA API, files, model calls, or threads.
- `commands.py`: automatic/manual display uses the same request ID, role, expiry, and duplicate checks.
  The notification API is called on the game thread, and suggested actions are not executed.

## Resource and cost boundaries

The game polls at most once per second, only when there is a request, and keeps only one pending poll and
one received answer. The server limits file lookups to at most 4 per second per authenticated connection
(at most 4 concurrent connections). No directory/full-memory search was added.

**1 second is this UX design's default value, not an EA requirement or a confirmed engine cycle.**
New turn polling does not call API/model generation functions. Polling/connection retries are separated from paid
model re-calls. Answers are not displayed after 180 seconds have passed since sending, but this does not mean that a paid call already started
is cancelled or refunded.

Household/lot/role changes, absence of a participating Sim from the lot, and bridge stop end automatic watching.
Corrupted answers/metadata, unconfirmed ACKs, and previous-turn answers are not displayed.
When the connection drops, the transport layer's inbox is cleared, and only turns that are still valid are re-polled.
UI failures are not retried every tick and are not disguised as character behavior.

## Verification

Full regression **330 passed** (14.616 seconds). 23 added to the previous 307.
Schema export matching and `git diff --check` also passed.

- Integration test connecting the real companion server + non-blocking game client + fake EA UI:
  multi-word Korean/emoji → 1 mock call → automatic notification call → manual read duplicate prevention.
- The automatic path above passes even when the game-side file read function is changed to fail.
- Passed a check that prohibited open/stat/mkdir/thread creation over 1,000 ticks in the pending state.
- Only 1 poll even across 10,000 callbacks with no time progression.
- Blocking of role/lot replacement, absence, expiry, prior manual display, old requests/wrong scope.
- UI reentry/exceptions, connection drops, unconfirmed ACKs, old-version negotiation, poll rate limiting.
- Provider calls do not increase even with repeated polling after failure/completion.

These checks do **not** prove **the actual game screen, Korean IME, or Zone.update frequency during pause**.

## Build and local installation

**9** production modules were built with the installed game's Python 3.7 runtime.
Each pyc magic is compared against the game. Diagnostic mode was not changed.

```text
dist/PlumbMate-alpha5.ts4script
SHA-256: 48040183c0e6feda2af84c4d0f903a1babb0ee44b82eaccd028dc37edafcee33
```

After confirming that The Sims was closed, only the single existing `00_PlumbMate_Test/PlumbMate.ts4script`
file in the actual Mods was replaced, and the SHA-256 match of the installed copy was confirmed.
The backup of the previous mod is kept in `dist/install-backups/2026-09-09-alpha5/`.
Saves were also copied as a precaution before the play test under `dist/private-playtest-backups/2026-09-09/`.
Both folders are excluded from git, and no game data or RP original text is uploaded.

The companion app was restarted with the new code's `run --provider mock --max-turns 8`.
`doctor --connect` result against the actual install path: all checks succeeded, **api_calls: 0**.

## Verification still blocked and next order

An attempt to open The Sims via CUA returned **the Mac is locked and cannot be unlocked automatically**.
Unlocking is needed. Therefore no household was loaded and the original save was not changed this time,
and the actual role selection window/Korean input box/automatic notification have not yet been verified on screen.

After unlocking:

1. `plumbmate.setup` role selection in a test household separate from the original (at least 2 Sims).
2. Test direct Korean input/paste/composition/cancel/long input in the `plumbmate.chat` window.
3. Confirm automatic notification receipt without `plumbmate.read` and blocking of duplicate read.
4. Real-game smoke test of the pause state, role/lot transitions, and companion app stop/reconnect.
5. After this path passes, implement console-free menu entry as the next small unit.

No main merge or public release is done. This version must not be presented as an autonomous AI partner or a finished
memory system.
