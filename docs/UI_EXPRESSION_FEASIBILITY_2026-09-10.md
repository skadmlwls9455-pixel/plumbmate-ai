# Emotion Records and Balloon UI — Pre-Implementation Feasibility Review

_English translation of UI_EXPRESSION_FEASIBILITY_2026-09-10_KO.md. If the two differ, the Korean original is authoritative._

Status: investigation/proposal only. No feature additions, no game emotion changes, no UI tuning overwrites.
The code objects of install 1.127.41.1230 / Python 3.7 were checked statically.
Game modules were not imported or executed via this path. Symbol existence is not evidence of real-game success.

## Four different goals

| Goal | Estimated implementation burden | Key boundary |
|---|---|---|
| View PlumbMate's recent conversation/emotion records in a separate read-only window | UI small–medium; permanent storage separately medium–large | Separate dialogue/observation/interpretation; session-only before save isolation |
| Integrate into the Sentiment cards of the existing relationship profile | Medium–large | Not simple display; affects relationship/effect/duration logic |
| Select and display existing icon-style speech/thought balloons | Small–medium prototype | Checks for resource existence, game thread, hidden Sims, duplicates, cooldown |
| Display free-form Korean sentences in overhead balloons | Large / undetermined | No free-text argument in the confirmed native path. Separate UI research needed |

Whether the 'emotion note' the user mentioned is the relationship profile's sentiments, the notebook, or photo memories
has not yet been confirmed. These are not treated as the same feature.
The workload is a design judgment, not a measured runtime performance or a guarantee of completion time.

## Local evidence

The following are names read from the install files, not guessed identifiers.

- `BalloonRequest.__init__`: arguments sim, icon, icon_object, overlay, balloon_type,
  priority, duration, delay, delay_randomization, category_icon, view_offset,
  rel_track, icon_info. No free-text argument was found.
- `BalloonRequest.distribute`: hidden Sim check and the `AddBalloon` / distributor path.
- `AddBalloon.write`: serializes messages for icon resource/object, type, priority, duration, etc.
- `BalloonTypeEnum`: THOUGHT / SPEECH / DISTRESS / SENTIMENT / SENTIMENT_INFANT.
- `trigger_passive_balloon` also exists, but a manual request does not guarantee arbitrary content selection/correct RP expression.
  Unconfirmed console command strings are not proposed.
- `NotebookTrackerSimInfo`: `generate_notebook_information`, `save_notebook`,
  `load_notebook`, `save_notebook_notes` exist. Notebook `_notes` can overwrite existing user
  notes, so it is not borrowed as PlumbMate storage.
- `SentimentTrack`: besides duration, sign, and display name, it is also connected to proximity loot applied when
  near the target Sim and to transitions upon age changes. It is not added arbitrarily as a decorative note.
- `buffs.memory` is also a separate system. Having 'memory' in a file does not make it the same as PlumbMate's permanent RP
  memory DB. `server_commands.memory_commands` is Python heap/GC diagnostics and is unrelated to this user record UI.

SHA-256 of the inspected files:

```text
balloon/balloon_request.pyc ff2fd57720e39b01a0907a8b06e35c56d2a503b2bb05398b1451392886a63be8
balloon/balloon_ops.pyc 0d44f366fd1d53bf9dec11fec335bbf4e4d6a17c456e0b66a39acca543930012
balloon/balloon_enums.pyc 551cd243c0227c1d046a59bdbf54d8ca64863d9c469e97219c9b05367ac981a2
notebook/notebook_tracker.pyc 0d349f915b2421af7679e1796413ba3b3b49c74374610a23e5d8c02d47fd03a5
relationships/sentiment_track.pyc 900ebd6f989c156b3643f0803f7dcd207e1f3628c4bc696e558419e9e5498e36
```

Public creator example: [sims4me's balloon expansion mod](https://www.curseforge.com/sims4/mods/conversationtweak-more-diverse-speech-and-thought)
provides more varied balloons based on events and traits, and warns of conflicts with mods that change the same balloon tuning
and of empty balloons due to patch/resource differences. This is supporting evidence of feasibility
and does not replace PlumbMate compatibility verification. It was not downloaded/installed.

## Recommended separated structure — not yet implemented

1. **Record model:** actor/target direction, observation evidence, distinguishing expression from fact.
   The line 'Shall we go for a walk together?' is not stored as a 'went for a walk together' event.
2. **Expression policy:** select a permitted cue from the topic/tone of validated output. The model is not allowed to freely generate icon
   resource IDs or execution commands. The model is not called for every balloon.
3. **Balloon adapter:** displays only its own requests, runs on the game thread, discarded on lot change.
   Silently skipped on missing resources, inactive/hidden Sims, or competition with other balloons.
4. **Record view:** shows only a limited set of recent items on request. Text answers stay on the verified notification wall.
   Raw inner thoughts are not disclosed by default; if desired, only records readable by the player are shown.

Proposed initial limits: 1 pending cue per Sim, short display and cooldown, record window shows the most recent 20.
Exact intervals/display times are decided after real game experiments. Constant scanning of all Sims/memories is prohibited.
**The expression itself** in this configuration is possible without additional API, but emotion interpretation/long-term retrospective generation
may require separate model calls and a token budget. Performance figures have not been measured yet.

## Improvement priorities

1. Real-game verification of the installed alpha.6's input guidance/expiry notification.
2. Console-free role setup and conversation entry, and easy checking of the current recipient/connection status.
3. An isolated capability probe that displays only 1 icon balloon. Even if it appears on screen, do not record it as an actual
   conversation/game action success.
4. Session-only record view → permanent records after validated save isolation.
5. Evaluation of actual model RP quality including persona/observation context. Naturalness is not solved by UI decoration alone.
   The mock is not dressed up to seem more human in order to hide the implementation state.
