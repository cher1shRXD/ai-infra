#!/usr/bin/env python3
"""Install Context Hub collection for every local Codex project session."""

import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent
TARGET = Path.home() / ".codex" / "hooks.json"
CONFIG = Path.home() / ".codex" / "config.toml"
COMMAND = f'/usr/bin/python3 "{ROOT / "apps/context-hooks/hooks/session_end.py"}" --provider codex'
NOTIFY = f'notify = ["/usr/bin/python3", "{ROOT / "scripts/codex_notify.py"}"]'


def main() -> None:
    payload = {}
    if TARGET.is_file():
        payload = json.loads(TARGET.read_text(encoding="utf-8"))
    hooks = payload.setdefault("hooks", {})
    for event, timeout in (("Stop", 10), ("SessionEnd", 3)):
        groups = hooks.setdefault(event, [])
        already_installed = any(
            handler.get("command") == COMMAND
            for group in groups
            for handler in group.get("hooks", [])
        )
        if not already_installed:
            groups.append(
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": COMMAND,
                            "timeout": timeout,
                            "statusMessage": "Saving AI session to Context Hub",
                        }
                    ]
                }
            )
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(TARGET, 0o600)
    config = CONFIG.read_text(encoding="utf-8") if CONFIG.is_file() else ""
    existing = re.search(r"(?m)^notify\s*=.*$", config)
    if existing:
        if "scripts/codex_notify.py" not in existing.group():
            raise RuntimeError("existing Codex notify command must be merged manually")
        config = config[: existing.start()] + NOTIFY + config[existing.end() :]
    else:
        config = NOTIFY + "\n\n" + config
    CONFIG.write_text(config, encoding="utf-8")
    os.chmod(CONFIG, 0o600)
    print(f"Installed Codex collection callbacks: {TARGET}, {CONFIG}")


if __name__ == "__main__":
    main()
