#!/usr/bin/env python3
"""Create local pilot credentials without printing secrets."""

import json
import argparse
import getpass
import os
from pathlib import Path
import secrets


ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
CLIENT_CONFIG = Path.home() / ".context-hub" / "config.json"


def read_env() -> dict[str, str]:
    values = {}
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rotate", action="store_true")
    args = parser.parse_args()
    values = read_env()
    key = values.get("CONTEXT_BOOTSTRAP_API_KEY", "")
    if args.rotate or len(key) < 32 or key == "local-dev-key-change-me-now":
        key = "ctx_" + secrets.token_urlsafe(48)
    values.update(
        {
            "CONTEXT_BOOTSTRAP_API_KEY": key,
            "CONTEXT_BOOTSTRAP_TENANT": values.get(
                "CONTEXT_BOOTSTRAP_TENANT", "local-pilot"
            ),
            "CONTEXT_BOOTSTRAP_PRINCIPAL": values.get(
                "CONTEXT_BOOTSTRAP_PRINCIPAL", "pilot-admin"
            ),
            "CONTEXT_BOOTSTRAP_PROJECTS": values.get(
                "CONTEXT_BOOTSTRAP_PROJECTS", "*"
            ),
            "CONTEXT_SESSION_RETENTION_DAYS": values.get(
                "CONTEXT_SESSION_RETENTION_DAYS", "90"
            ),
        }
    )
    ENV_FILE.write_text(
        "".join(f"{name}={value}\n" for name, value in sorted(values.items())),
        encoding="utf-8",
    )
    os.chmod(ENV_FILE, 0o600)

    CLIENT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    client = {
        "url": "http://127.0.0.1:8000",
        "api_key": key,
        "project_id": "general",
        "user": getpass.getuser(),
    }
    CLIENT_CONFIG.write_text(
        json.dumps(client, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(CLIENT_CONFIG, 0o600)
    print(f"Created local server config: {ENV_FILE}")
    print(f"Created local client config: {CLIENT_CONFIG}")


if __name__ == "__main__":
    main()
