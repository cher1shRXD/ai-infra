#!/usr/bin/env python3
"""Install a daily local backup job with launchd."""

import os
from pathlib import Path
import plistlib
import subprocess


ROOT = Path(__file__).resolve().parent.parent
LABEL = "com.company.context-hub.backup"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG = Path.home() / ".context-hub" / "backup.log"


def main() -> None:
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        "ProgramArguments": [str(ROOT / "scripts/backup.sh")],
        "WorkingDirectory": str(ROOT),
        "StartCalendarInterval": {"Hour": 3, "Minute": 0},
        "StandardOutPath": str(LOG),
        "StandardErrorPath": str(LOG),
        "ProcessType": "Background",
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        },
    }
    PLIST.write_bytes(plistlib.dumps(payload))
    os.chmod(PLIST, 0o600)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(PLIST)], check=False, capture_output=True)
    subprocess.run(["launchctl", "bootstrap", domain, str(PLIST)], check=True)
    print(f"Installed daily backup job: {PLIST}")


if __name__ == "__main__":
    main()
