# Socket Bridge Reliability Fix Log

_English translation of BRIDGE_FIXES_2026-09-05_KO.md. If the two differ, the Korean original is authoritative._

Baseline: four defects reproduced at `81da97c`. The fix scope is the local repository; no GitHub push, no replacement of the actual Mods, and no game launch or save was performed.

## Fixes and regression verification

| Defect | Fix | Regression test |
|---|---|---|
| Hooks of other mods were also removed because of markers copied by `functools.wraps` | Ownership is checked using the actual wrapper object stored on the class. If another mod is on the outside, the chain is not changed and only the inner PlumbMate wrapper is deactivated | Copied markers and marker-less wrappers preserved, pump only once on reinstall, driver replaced on module reload |
| An internal hook remaining after stop re-ran the binder | Driver suspend, binder release, and client detach/close are performed independently. Stops regardless of whether a socket can be created | stop under two kinds of other-mod wrappers, 0 reconnects even over 100 ticks and calls to old direct callbacks, returns only via explicit start |
| After the 16-per-tick limit, ACKs left in the buffer stalled waiting for a new recv | Frames already complete in memory are drained first, then a non-blocking recv. Frame count and read-byte caps kept | Receive 64 ACKs in one batch and process all of them in 4 pumps, partial frames, ACK just before EOF |
| Endpoint/token file lookups and directory handling on every 5-second game tick | Settings are read only at mod load/explicit start and kept in memory. CLI reuses a persistent port; client reconnects to the same port with backoff | 0 open/exists/isdir/makedirs calls over 200 ticks including binding, late app start, restart on same port, settings reload on intentional port change |

Problems found at the same receive boundary were also fixed. Both client and server apply the size cap even to completed frames that include a newline. Unknown/duplicate ACKs do not increase the success count. Invalid ports are rejected before connecting. The binder's clock exceptions and unexpected driver exceptions are kept from propagating outside the game wrapper. Exceptions from the original game method itself are not hidden.

## Changed connection contract

- The token is prepared before the game starts. The initial port is `127.0.0.1:47651`, and subsequent `listen` reuses the stored port.
- The endpoint file is a persistent setting, not a liveness signal. It is not deleted when the app exits.
- If the port is occupied, it shows a failure without killing another process or moving to an arbitrary port.
- After an intentional port/token change, or creation of a missing token while running, the settings are re-read via `plumbmate.bridge.start` or a game restart. An ordinary companion app restart does not require this.
- No in-game worker thread, file polling, blocking connect/recv/send, or DNS lookup was added. Initial settings reading and file I/O for manual commands are a separate boundary.
- An ACK means acceptance into the companion app's in-memory queue and is not evidence of SQLite persistent storage or a successful game save. Events whose connection dropped after sending are not resent, per the existing at-most-once policy.

## Verification scope

Verification commands:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=companion/src python3 -m unittest discover -s tests
python3 game_mod/build_game_mod.py
git diff --check
```

- All tests passed, having grown from the previous 164 to 189. Network tests use only temporary local loopback connections and require no game, API key, or external model call.
- The three game modules were compiled with the installed `libpython370.dylib`, and the build confirmed they match the installed bytecode magic. Host Python tests and the game Python 3.7 build are different verifications.
- Output: `dist/PlumbMate.ts4script`. Actual game loading of the fixed build, long play sessions, and real coexistence with other mods have not yet been re-verified. The tests' fake Zone/wrappers are not treated as completion of WW/MCCC compatibility.
- Automatic game event observation, real action execution, save commit integration, and long-term memory are not completed by this fix.
