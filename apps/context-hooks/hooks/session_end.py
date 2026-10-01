#!/usr/bin/env python3
"""Spool and upload a completed Codex or Claude Code transcript."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MAX_TRANSCRIPT_BYTES = 20_000_000
DEFAULT_URL = "http://interns-Mac-mini-2.local:8000"
SECRET_PATTERNS = (
    (re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"']+"), r"\1[REDACTED]"),
    (
        re.compile(r'(?i)(["\']?(?:api[_-]?key|token|password|secret)["\']?\s*[:=]\s*["\']?)[^\s,"\']+'),
        r"\1[REDACTED]",
    ),
    (re.compile(r"\b(?:sk|ctx)_[A-Za-z0-9_-]{16,}\b"), "[REDACTED_TOKEN]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\bgh[opsu]_[A-Za-z0-9]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
        "[REDACTED_PRIVATE_KEY]",
    ),
)


def local_user() -> str:
    return pwd.getpwuid(os.getuid()).pw_gecos.split(",", 1)[0] or getpass.getuser()


def redact(text: str) -> str:
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def outbox() -> Path:
    root = (
        os.getenv("PLUGIN_DATA")
        or os.getenv("CLAUDE_PLUGIN_DATA")
        or str(Path.home() / ".context-hub")
    )
    path = Path(root) / "session-outbox"
    path.mkdir(parents=True, exist_ok=True)
    return path


def client_config() -> tuple[str, str | None]:
    url = os.getenv("CONTEXT_HUB_URL")
    api_key = os.getenv("CONTEXT_HUB_API_KEY")
    config_path = Path(
        os.getenv("CONTEXT_HUB_CONFIG", str(Path.home() / ".context-hub/config.json"))
    )
    config = {}
    if (not url or not api_key) and config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            url = url or config.get("url")
            api_key = api_key or config.get("api_key")
        except (OSError, json.JSONDecodeError):
            pass
    url = (url or DEFAULT_URL).rstrip("/")
    if not api_key:
        user = os.getenv("CONTEXT_HUB_USER") or config.get("user") or local_user()
        request = Request(
            url + "/v1/register",
            data=json.dumps({"principal": user}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=2.5) as response:
                issued = json.load(response)
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                json.dumps(
                    {
                        "url": url,
                        "api_key": issued["api_key"],
                        "key_id": issued["id"],
                        "project_id": "general",
                        "user": user,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            os.chmod(config_path.parent, 0o700)
            os.chmod(config_path, 0o600)
            api_key = issued["api_key"]
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError):
            return url, None
    return url, api_key


def post(path: Path, url: str, api_key: str) -> bool:
    request = Request(
        url + "/v1/sessions",
        data=path.read_bytes(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=2.5) as response:
            return response.status in (200, 202)
    except (HTTPError, URLError, TimeoutError, OSError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True, choices=("codex", "claude"))
    args = parser.parse_args()
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    transcript_path = event.get("transcript_path")
    session_id = event.get("session_id")
    if not transcript_path or not session_id:
        return 0
    try:
        raw = Path(transcript_path).read_bytes()
    except OSError:
        return 0
    truncated = len(raw) > MAX_TRANSCRIPT_BYTES
    raw = raw[:MAX_TRANSCRIPT_BYTES]
    payload = {
        "provider": args.provider,
        "provider_session_id": str(session_id),
        "model": event.get("model"),
        "cwd": event.get("cwd"),
        "transcript": redact(raw.decode("utf-8", errors="replace")),
        "transcript_format": "jsonl",
        "transcript_truncated": truncated,
    }
    filename = hashlib.sha256(f"{args.provider}:{session_id}".encode()).hexdigest() + ".json"
    queued = outbox() / filename
    temporary = queued.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(queued)

    url, api_key = client_config()
    if not api_key:
        return 0
    for item in sorted(outbox().glob("*.json"))[:5]:
        if post(item, url, api_key):
            item.unlink(missing_ok=True)
        else:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
