"""Slack -> Claude Code bridge.

Each @-mention in Slack becomes one turn of a single, persistent Claude Code
session (via `claude -p --resume <id>`). State lives on disk in
$CLAUDE_PROJECT_DIR; reset it by mentioning the bot with `reset`.
"""
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

# Load env from secrets/.env (preferred), fall back to project-root .env.
HERE = Path(__file__).parent.resolve()
for candidate in (HERE / "secrets" / ".env", HERE / ".env"):
    if candidate.exists():
        load_dotenv(candidate)
        break

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("claude-slack")

CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")
PROJECT_DIR = Path(os.environ["CLAUDE_PROJECT_DIR"]).expanduser().resolve()
SESSION_FILE = PROJECT_DIR / ".claude_slack_session"
PERMISSION_MODE = os.environ.get("CLAUDE_PERMISSION_MODE", "acceptEdits")
TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT_SECONDS", "600"))

session_lock = threading.Lock()
MENTION_RE = re.compile(r"^<@[^>]+>\s*")


def load_session_id():
    if SESSION_FILE.exists():
        return SESSION_FILE.read_text().strip() or None
    return None


def save_session_id(sid):
    SESSION_FILE.write_text(sid)


def clear_session_id():
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()


def run_claude(message):
    """One headless turn. Resumes the saved session if there is one."""
    cmd = [
        CLAUDE_BIN,
        "-p",
        "--output-format", "json",
        "--permission-mode", PERMISSION_MODE,
    ]
    sid = load_session_id()
    if sid:
        cmd += ["--resume", sid]
    cmd.append(message)

    log.info("claude turn start (resume=%s, msg=%r)", sid or "<none>", message[:200])
    started = time.monotonic()
    proc = subprocess.run(
        cmd,
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
        check=False,
    )
    elapsed = time.monotonic() - started

    if proc.returncode != 0:
        log.warning("claude exit=%d in %.1fs stderr=%r", proc.returncode, elapsed, proc.stderr[:500])
        # If --resume failed (e.g. session was deleted), retry without it.
        if sid and "resume" in (proc.stderr or "").lower():
            log.info("clearing stale session id and retrying without --resume")
            clear_session_id()
            return run_claude(message)
        return f"```\nclaude exited {proc.returncode}\n{proc.stderr.strip()}\n```"

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        log.warning("non-JSON stdout from claude: %r", proc.stdout[:500])
        return f"```\n{proc.stdout[:3000]}\n```"

    new_sid = data.get("session_id")
    if new_sid:
        save_session_id(new_sid)
    log.info("claude turn ok in %.1fs (session=%s, reply_len=%d)",
             elapsed, new_sid or sid, len(data.get("result") or ""))
    return data.get("result") or "(claude returned no result)"


app = App(token=os.environ["SLACK_BOT_TOKEN"])


@app.event("app_mention")
def handle_mention(event, client):
    text = MENTION_RE.sub("", event.get("text", "")).strip()
    channel = event["channel"]
    thread_ts = event.get("thread_ts") or event["ts"]
    user = event.get("user", "?")
    log.info("mention from user=%s channel=%s thread=%s text=%r",
             user, channel, thread_ts, text[:200])

    if text.lower() in {"reset", "new", "/new"}:
        clear_session_id()
        log.info("session reset by user=%s", user)
        client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text="Cleared. Next mention starts a fresh Claude session.",
        )
        return

    if not text:
        client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text="Mention me with a message. `reset` clears the session.",
        )
        return

    placeholder = client.chat_postMessage(
        channel=channel, thread_ts=thread_ts, text="_thinking…_"
    )

    def work():
        try:
            with session_lock:
                reply = run_claude(text)
        except subprocess.TimeoutExpired:
            log.warning("claude timed out after %ds", TIMEOUT)
            reply = f"Claude timed out after {TIMEOUT}s."
        except Exception as e:
            log.exception("claude run failed")
            reply = f"Error: {e}"
        client.chat_update(channel=channel, ts=placeholder["ts"], text=reply)
        log.info("posted reply to channel=%s thread=%s (len=%d)",
                 channel, thread_ts, len(reply))

    threading.Thread(target=work, daemon=True).start()


@app.event("message")
def ignore_messages():
    pass


if __name__ == "__main__":
    log.info("starting bot")
    log.info("project dir: %s", PROJECT_DIR)
    log.info("session file: %s (exists=%s)", SESSION_FILE, SESSION_FILE.exists())
    log.info("permission mode: %s", PERMISSION_MODE)
    log.info("claude bin: %s", CLAUDE_BIN)
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
