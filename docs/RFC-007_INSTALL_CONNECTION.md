# RFC-007 Partial Specification — Installation Safety and Connection Diagnostics

_English translation of RFC-007_INSTALL_CONNECTION_KO.md. If the two differ, the Korean original is authoritative._

Status: design/acceptance conditions + partial implementation. The Windows executable and connection test UI are not implemented. In addition to the file bridge, a loopback socket and a game tick client are implemented. Failure cases of other products that were passed along to us are test candidates; this document has not verified the source or incidence of those cases.

## 1. AV and distribution

- Fully disabling Defender, excluding the entire Mods/user folder, or forcing administrator privileges are not made mandatory installation steps.
- Display the official distribution URL, signing issuer, release hash, and clean build provenance. When a false positive is suspected, first guide the user to check the quarantine history and the verification/reporting procedures of the author/security vendor. Restoration/exclusions are not applied automatically before verification.
- Code signing, timestamping, and verification of distribution integrity are included in the release checklist. It is not described as signing guaranteeing harmlessness or eliminating SmartScreen warnings/AV detection. The [Microsoft SmartScreen explanation](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation) distinguishes signing from file/issuer reputation.
- Security exclusions are not the default solution. Microsoft explains that scan exclusions reduce the scope of protection. [Microsoft Security app guidance](https://support.microsoft.com/en-us/windows/virus-and-threat-protection-in-the-windows-security-app-1362f4cd-d71a-b52a-0b66-c2820032b65e)

## 2. Connection test button

`연결 테스트` ("Connection test") checks only the local bridge, without API calls or charges. External model/API checks are a separate explicit action.

1. Check whether the companion app is running and the bridge format.
2. For the file bridge, check the path and accessibility. Temporary write tests use only a dedicated test file.
3. When the socket bridge is introduced, display loopback bind, port occupancy, reachability, session authentication, and protocol compatibility as distinct errors.
4. If there is no response within the time limit, the game yields to normal autonomous behavior. The UI provides reconnection status and a diagnostic ID.

On a port conflict, the owning process is not arbitrarily terminated. In advanced settings, change to an unused port or reassign automatically, confirm the settings on both sides, then reconnect. Addresses are limited to `127.0.0.1`/`::1`, and the problem is not solved by binding `0.0.0.0`. Session authentication is mandatory so that another app's listener is not mistaken for PlumbMate.

The current implementation uses IPv4 `127.0.0.1` and a fixed/persistent port scheme. CLI `listen` reuses the existing configured port, and the initial default is `47651`. The endpoint setting is not deleted on exit, and whether there is a connection is determined from the socket state. On conflict, an error is displayed and the port is not moved arbitrarily. If it was intentionally changed with `--port` or the token was rotated, the game re-reads the settings via `plumbmate.bridge.start` or a game restart. The token is prepared before the game starts; if it is not ready, the game tick does not poll files and waits for a settings update. File I/O within the game tick and background listeners inside the game are not used. UI-based updating of settings on both sides is future implementation.

## 3. Firewall

It is not assumed that loopback is unconditionally allowed by every security product/policy. However, a connection failure is not immediately blamed on the firewall; process, address family, port, and authentication are diagnosed first.

Allow rules are explained only when their necessity has been confirmed per product/policy, and the user approves. Turning off the firewall entirely, or allowing all public-network/remote inbound traffic, is not advised. Organization-managed policies are not bypassed. The installer does not add security rules automatically.

## Acceptance conditions

- Display AV quarantine, unsigned/newly signed, app not running, port occupied, authentication failure, protocol mismatch, IPv4/IPv6 mismatch, and restart as distinct cases.
- During the connection test: API cost 0, game-thread network waiting 0, termination of other processes 0.
- Diagnostic exports remove keys, chats, Sim names, and personal paths by default and provide a user preview.
- Windows and macOS are each tested in an actual distribution environment. Current Mac experiment results are not labeled as Windows compatibility complete.
