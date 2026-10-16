# Gaps Between the Game and RP Expression

_English translation of GAME_RP_GAPS_2026-09-10_KO.md. If the two differ, the Korean original is authoritative._

## This fix: speaker portrait

The existing `notify` passed only `owner`. Inspection of `UiDialogNotification.build_msg` in the install
showed that owner does not automatically guarantee an icon, and `icon_override` is a separate path.
Passing `distributor.shared_messages.IconInfoData(obj_instance=partner)` to show_dialog
was added in alpha.7. It uses the validated partner object, not the currently selected Sim.

- The portrait uses EA's existing icon path. No image generation/file copying/API calls.
- If icon preparation fails, only the name/body is displayed. No retry after show_dialog.
- Connection failure, cost, and wait-expiry messages keep a system title so they do not look like something the character said,
  and the owner portrait is not automatically attached. There is no dedicated system image yet.
- Basic roles/conversation/automatic notifications were confirmed in the real game in alpha.5. **The 2026-09-11 alpha.7 portrait was also confirmed in the real game.**
  With player Summer selected, AI Travis's face/name was displayed in the answer notification,
  and a manual read re-call was blocked as an already-displayed answer. No face is attached to the system acceptance notification.
- 341 regression tests passed (14.776 seconds). Build magic check passed.

## Consistency takes priority over immersion

| Boundary | Current state | Next acceptance criterion |
|---|---|---|
| Who is speaking | player/partner separated, partner portrait added to dialogue | Face/name/sending role match even when another Sim is selected |
| Dialogue vs. system message | Failure/expiry are system; mock is labeled | Errors are not disguised as RP like "I don't want to talk right now" |
| Suggestion vs. actual action | All action execution disabled | A walk suggestion is not promoted to a completed walk/memory |
| What the model knows vs. the actual scene | Currently centered on a small identity/household snapshot | Does not assert unobserved actions/outfits/emotions as if it saw them |
| Response generation vs. display | request/binding/scene/180-second limit | Does not show dialogue/portraits from a previous location after travel or a role change |
| Inner thoughts vs. dialogue | Inner-thought feature not implemented | Does not accidentally expose internal analysis in the notification body |
| Character settings vs. actual Sim | Roles are session-only, persona profiles not implemented | Does not assume an arbitrary Sim is Aiden on reconnect |
| Record vs. fact | Storing model memory patches is prohibited, permanent memory not implemented | Separate observations/player claims/interpretations and validate save scope |
| Simlish/game actions vs. Korean text | Only text is connected, no native conversation sync | Does not claim the two Sims actually talked just because a notification arrived |

The portrait supplements "who the speaker is," but it does not solve
the action synchronization in the last row. Until execution results are verified, the dialogue cannot guarantee that an action was completed.
In the future, observation grounds and a suggestion/dialogue distinction will be added to the output contract, but simple keyword checks
will not be promoted as general hallucination prevention.

## Small implementation order

1. Real-game confirmation of the portrait and regression of existing duplicate/role change/wait expiry.
2. Console-free entry point; recipient and connection status visible at a glance.
3. Limited observation snapshot + suggestion/completion distinction in output. Synthetic cases for evaluating actual model quality.
4. Connect expression and records only to verified native interaction execution/completion events.
5. Session records → persistent memory after save lineage validation. Bubbles are a separate opt-in expression adapter.

Generic emotional decoration or large-scale UI overwrites are not mixed into this fix.
