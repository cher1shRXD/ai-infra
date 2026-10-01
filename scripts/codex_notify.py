#!/usr/bin/env python3
"""Bridge Codex agent-turn-complete notifications to the session collector."""

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
COLLECTOR = ROOT / "apps/context-hooks/hooks/session_end.py"


def main() -> int:
    if len(sys.argv) != 2:
        return 0
    try:
        notification = json.loads(sys.argv[1])
    except json.JSONDecodeError:
        return 0
    if notification.get("type") != "agent-turn-complete":
        return 0
    session_id = notification.get("thread-id")
    if not session_id:
        return 0
    matches = list((Path.home() / ".codex" / "sessions").rglob(f"*{session_id}*.jsonl"))
    if not matches:
        return 0
    transcript = max(matches, key=lambda item: item.stat().st_mtime_ns)
    event = {
        "session_id": session_id,
        "transcript_path": str(transcript),
        "cwd": notification.get("cwd"),
        "hook_event_name": "Stop",
    }
    try:
        subprocess.run(
            [sys.executable, str(COLLECTOR), "--provider", "codex"],
            input=json.dumps(event),
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
