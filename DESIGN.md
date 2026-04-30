# Design notes

Captured from the conversation that produced this project. The README covers
*how* to run it; this file captures *why* it's built this way and what was
considered and rejected.

## Goal

Keep working on a project from a specific Claude Code session, but feed
messages to it from Slack instead of the terminal. State (history, edits,
todos) must persist across messages — each Slack mention is one turn in the
same long-running session.

## Options considered

1. **Anthropic's first-party Claude Slack app** (claude.ai connector).
   Easiest, but it runs on Anthropic's side. The Linux box isn't involved
   and there's no way to bind the conversation to a Claude Code session
   that has access to local files / shell. **Rejected**: doesn't satisfy
   "keep working on my project on my machine."

2. **DIY Slack bot calling the Anthropic Messages API directly.** Full
   control over the prompt, but you reimplement everything Claude Code
   already does — file tools, todos, permission system, session storage.
   **Rejected**: redoes work for no gain.

3. **Slack bot that drives `claude -p` headless on the Linux box.**
   Chosen. Each mention shells out to the Claude Code CLI; session
   continuity comes from `--resume <id>`.

## Why `--resume <session_id>` is the load-bearing mechanism

Claude Code persists every session to disk. `claude -p` (headless / non-
interactive) can resume any saved session by ID, so we get true session
state — not just a chat history we replay — across Slack messages.

Pattern:

```bash
# First turn: capture session_id from the JSON output
claude -p --output-format json "first message"
# JSON contains "session_id": "abc..."

# Every later turn: pin to that ID
claude -p --resume abc... --output-format json "next message"
```

`bot.py` saves the ID to `.claude_slack_session` inside `$CLAUDE_PROJECT_DIR`
and reuses it on each mention. `reset` deletes the file → next turn starts
fresh.

If `--resume` fails (session file deleted by user, etc.), the bot clears the
saved ID and retries without `--resume` — so it self-heals into a new
session rather than dying.

## Why Socket Mode

No public URL, no inbound webhook, no ngrok. The Linux box opens an
outbound websocket to Slack and receives events on it. Right fit for a
single-user bot running on a personal machine.

## Trade-offs accepted

- **One turn at a time.** A module-level `threading.Lock` serializes
  `claude -p --resume <same-id>` calls. Two parallel resumes against the
  same session would race on the session file. Cost: if you fire two
  mentions back-to-back, the second waits.
- **Permission mode is set once at bot start.** Default `acceptEdits` —
  auto-accepts edits but still blocks shell etc. Headless mode can't show
  approval prompts, so the choice is: accept-edits (safe-ish, may stall on
  bash), or `bypassPermissions` (fully autonomous, riskier). Per-tool
  pre-approval lives in the *target project's* `.claude/settings.json`,
  not in this bot.
- **3-second Slack ack.** Bolt's handler returns immediately by posting a
  `_thinking…_` placeholder, then a worker thread runs Claude and edits
  the placeholder when done. Avoids Slack retrying the event.
- **Shell-out, not Agent SDK.** Cheaper to build and debug; output is just
  one JSON blob to parse. If we ever need streaming partial output into
  Slack as Claude works, switching to the Agent SDK would be the move.

## Known caveats (also in README)

- Public channels = anyone in them can drive Claude with whatever
  permissions are configured. Use a private channel.
- Slack stores messages indefinitely — don't paste secrets at the bot.
- Long turns are killed at `CLAUDE_TIMEOUT_SECONDS` (default 600).

## Things deliberately *not* built

- No multi-session-per-channel routing. One project, one session, one bot.
  If we need per-thread sessions later, key the session file by `thread_ts`.
- No streaming partial output to Slack. Single edit when the turn finishes.
- No auth / ACL beyond Slack channel membership.
- No retry / queue. If `claude` crashes mid-turn, the user sees the error
  and re-mentions.
