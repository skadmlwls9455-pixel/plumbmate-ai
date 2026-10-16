# PlumbMate AI — One-Page Portfolio Introduction

_English translation of PORTFOLIO_KO.md. If the two differ, the Korean original is authoritative._

## One-line introduction

**An AI 2P companion for The Sims 4 that plays alongside the player rather than in their place.**

The Sim played by the player and the Sim played by the AI live in the same world. The AI Sim briefly reads the current
situation, explains its intent in Korean, and requests only actions the player has permitted from the
game.

## The problem it aims to solve

Existing AI character projects tend to become detached from the actual game state even when the conversation is fun,
and fully automatic control can undermine the player's sense of control and the safety of save data. Also,
approaches that continuously send the screen carry a heavy privacy and API cost burden.

PlumbMate aims for the space in between.

- It combines the AI's conversation and game action intent into a single response.
- The AI first says "what it wants to do and why," and the player approves.
- Instead of arbitrary code or keyboard control, it uses only a pre-approved list of actions.
- It sends only a minimal structured state, not screenshots or save files.

## Currently implemented MVP

```text
The Sims 4 → small state JSON → local companion app → AI or free Mock
     ↑                                              ↓
     └──────── validated Korean answer + action suggestion JSON ────────┘
```

- Talk to Aiden with `plumbmate.chat <메시지>` (`<메시지>`: "<message>").
- The companion app generates a short Korean answer and an action suggestion.
- Check the latest answer with `plumbmate.read`.
- There is a Mock mode that runs without a network, so demos and tests cost nothing.
- Actual game action execution is still turned off. It will be connected in the next stage together with an approval UI.

## Token and cost saving design

1. **No constant observation** — calls are made only when a conversation or an important event occurs, not every frame.
2. **Send only state changes** — after the first request, only the changed need, relationship, and situation fields are sent.
3. **Local memory** — recent relationship facts are stored compactly on the device, and only the needed portion is put into the prompt.
4. **Short structured responses** — instead of long free text, dialogue, reason, and permitted action are received as JSON, with an output cap.
5. **Stable prompt prefix** — personality and safety rules are fixed so the prompt cache is easy to reuse.
6. **Model routing** — everyday conversation uses a low-cost model; only rare long-term story design selects a higher-tier model.
7. **Deduplication** — designed so that low-risk reactions repeated in the same state can be handled by a local cache.

The current API response cap is 220 tokens. The model ID is not hard-coded, so users can choose cost and
performance themselves.

## Safety and privacy

- The API key is not put into the `Mods` folder or game files.
- The game process does not connect to the internet directly.
- Items that may be sent externally are limited by a public JSON schema.
- The screen, saves, Tray, and CC files are not sent.
- Model responses are not trusted and are checked against the schema and the action allow-list.
- The public version shows a send example, the provider, whether data is retained, and a history deletion feature before first run.

## What sets it apart

The goal is not to stick a chatbot onto a Sim's face but to build **an explainable, approvable AI co-player**.
Aiden is not an NPC that generates speech but a second player who, in agreement with the player,
chooses dates, photos, chores, and relationship events.

## 90-second demo scenario

1. Show a scene with a couple of Sims at home and the companion app's Mock mode.
2. Type "Aiden, shall the two of us take photos today and go on a dinner date?"
3. Check Aiden's short answer and the `suggest_only` action suggestion.
4. Show that the sent JSON contains only small state changes instead of screenshots.
5. Show the options `승인 / 다른 제안 / 이번에는 내가 할게` ("Approve / Another suggestion / I'll do it this time") as a next-version mockup.

## Next milestones

- Command registration and read/write smoke tests in the actual game
- Approval UI and action requests with an expiry time
- Safe execution adapters for conversation, self actions, and cancelling the current action
- Gemini and local model providers
- Korean UI/manual and Windows/macOS packaging
- Shared memories, date planning, and jealousy/boundary settings for couple play

## Distribution direction

GitHub for source and issue management; for installable demos, a release page that can clearly disclose
external model communication. Before going public, a per-game-patch compatibility table, privacy notice,
Mock demo, and deletion instructions will be provided together.
