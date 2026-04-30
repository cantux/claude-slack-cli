# claude-cli-from-slack

A tiny Slack bot that forwards every @-mention to a single, persistent
[Claude Code](https://docs.claude.com/en/docs/claude-code) session running on
this Linux machine. Each Slack message becomes one turn in the same session
via `claude -p --resume <session_id>`, so file edits, todos, and conversation
history carry across messages.

## How it works

1. Slack Bolt listens in Socket Mode (no public URL needed).
2. On `app_mention`, the bot strips the leading `<@BOT>` and shells out to
   `claude -p --output-format json --resume <id> "<message>"` inside
   `$CLAUDE_PROJECT_DIR`.
3. The returned `session_id` is saved to `.claude_slack_session` in that
   project, so the next mention resumes the same session.
4. Reply is posted back into the same Slack thread.
5. Mention the bot with `reset` to start a fresh session.

## Setup

### 1. Create the Slack app (one-time)

At <https://api.slack.com/apps> → **Create New App** → **From scratch**.

- **Socket Mode**: enable. Generate an **App-Level Token** with scope
  `connections:write`. This is `SLACK_APP_TOKEN` (`xapp-...`).
- **OAuth & Permissions** → Bot Token Scopes: `app_mentions:read`,
  `chat:write`. Install to workspace. Copy the **Bot User OAuth Token** —
  this is `SLACK_BOT_TOKEN` (`xoxb-...`).
- **Event Subscriptions**: enable, subscribe to bot event `app_mention`.
- Invite the bot to a channel: `/invite @YourBotName`.

### 2. Install on Linux

```bash
cd ~/Projects/claude-cli-from-slack
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure (one-time)

The bot reads its config from `secrets/.env` (gitignored). Copy the template
once and fill in your tokens:

```bash
mkdir -p secrets
cp .env.example secrets/.env
chmod 600 secrets/.env
vim secrets/.env       # paste SLACK_BOT_TOKEN, SLACK_APP_TOKEN, set CLAUDE_PROJECT_DIR
```

`secrets/.env` is the canonical location and survives any project-root
cleanup. (A `.env` at the project root is still picked up as a fallback if
`secrets/.env` is missing.)

Make sure `claude` is on PATH (or set `CLAUDE_BIN` in `secrets/.env`):

```bash
which claude
```

### 4. Run

For a quick foreground test:

```bash
source .venv/bin/activate
python bot.py
```

You should see `⚡️ Bolt app is running!`. In Slack:
`@YourBotName look at src/foo.py and tell me what it does`.

For normal use, run it under systemd — see next section.

## Permission modes

`claude -p` cannot show interactive approval prompts, so set
`CLAUDE_PERMISSION_MODE` to one of:

- `acceptEdits` (default here) — auto-accept file edits, still blocks shell
  and other tools that need approval. Safest useful default.
- `bypassPermissions` — fully autonomous. Use only if you trust the bot's
  Slack channel and the project it's pointed at.
- `plan` — read-only; Claude proposes but doesn't change anything.

You can also pre-approve specific tools in the project's
`.claude/settings.json` under `permissions.allow` so `acceptEdits` is enough
for your normal workflow.

## Run as a service

Drop this into `~/.config/systemd/user/claude-slack.service`:

```ini
[Unit]
Description=Slack -> Claude Code bridge
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/Projects/claude-cli-from-slack
ExecStart=%h/Projects/claude-cli-from-slack/.venv/bin/python -u bot.py
Restart=on-failure
RestartSec=5
StandardOutput=append:%h/Projects/claude-cli-from-slack/bot.log
StandardError=append:%h/Projects/claude-cli-from-slack/bot.log

[Install]
WantedBy=default.target
```

Then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now claude-slack
systemctl --user status claude-slack
tail -f ~/Projects/claude-cli-from-slack/bot.log
```

Notes on this unit:

- `python -u` disables stdout buffering so log lines appear immediately.
- `StandardOutput`/`StandardError` go to `bot.log` because journald isn't
  capturing user-service output on this box (no `/var/log/journal/`). If
  your system has persistent journald, you can drop those two lines and use
  `journalctl --user -u claude-slack -f` instead.

To restart after changing `secrets/.env` or `bot.py`:

```bash
systemctl --user restart claude-slack
```

## Caveats

- **One session at a time.** A module-level lock serializes turns; don't run
  two bots against the same session file.
- **Long turns.** Slack edits the `_thinking…_` placeholder when Claude
  returns. If a turn exceeds `CLAUDE_TIMEOUT_SECONDS` (default 600s) it is
  killed.
- **Public channels are exposed surface area.** Anyone in the channel who
  can @-mention the bot can drive Claude with whatever permissions you've
  granted. Use a private channel.
- **Secrets in messages.** Slack messages are stored by Slack. Don't paste
  credentials into the bot.

## Reset

```
@bot reset
```

…or delete `$CLAUDE_PROJECT_DIR/.claude_slack_session` directly.
