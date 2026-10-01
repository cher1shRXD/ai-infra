#!/usr/bin/env python3
"""Dependency-free Context Hub client for shared agent skills."""

import argparse
import json
import os
from pathlib import Path
import socket
import sys
from typing import Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_URL = "http://interns-Mac-mini-2.local:8000"


def config_path() -> Path:
    return Path(
        os.getenv("CONTEXT_HUB_CONFIG", str(Path.home() / ".context-hub/config.json"))
    )


def config() -> dict:
    path = config_path()
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def credentials() -> tuple[str, str]:
    values = config()
    url = (os.getenv("CONTEXT_HUB_URL") or values.get("url") or DEFAULT_URL).rstrip("/")
    api_key = os.getenv("CONTEXT_HUB_API_KEY") or values.get("api_key")
    if api_key and (os.getenv("CONTEXT_HUB_API_KEY") or values.get("agent_id")):
        return url, api_key
    principal = os.getenv(
        "CONTEXT_HUB_AGENT_ID", f"context-agent:{socket.gethostname()}"
    )
    request = Request(
        url + "/v1/register",
        data=json.dumps({"principal": principal}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        issued = json.load(response)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "url": url,
                "api_key": issued["api_key"],
                "key_id": issued["id"],
                "project_id": "general",
                "agent_id": principal,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(path.parent, 0o700)
    os.chmod(path, 0o600)
    return url, issued["api_key"]


def call(
    method: str, path: str, payload: Optional[Dict] = None
) -> Optional[Dict]:
    url, api_key = credentials()
    request = Request(
        url.rstrip("/") + path,
        data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read()
            return json.loads(body) if body else None
    except HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"Context Hub returned {exc.code}: {detail}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Context Hub unavailable: {exc}") from exc


def project() -> str:
    return os.getenv("CONTEXT_HUB_PROJECT_ID") or config().get("project_id") or "general"


def add_memory_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--kind", required=True, choices=("fact", "decision", "artifact", "handoff", "failure"))
    parser.add_argument("--subject", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--content", required=True)
    parser.add_argument("--namespace", default="project/general")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--source-type", default="agent_run", choices=("agent_run", "git", "issue", "document", "human"))
    parser.add_argument("--source-uri", required=True)
    parser.add_argument("--author", default=os.getenv("CONTEXT_HUB_AGENT_ID", "agent"))
    parser.add_argument("--performed-by", action="append", default=[])
    parser.add_argument("--run-id", default=os.getenv("CONTEXT_HUB_RUN_ID"))
    parser.add_argument("--confidence", type=float, default=0.8)


def memory_payload(args: argparse.Namespace) -> dict:
    if not args.performed_by:
        raise RuntimeError(
            "--performed-by is required; ask the user who did the work before recording it"
        )
    performed_by = args.performed_by
    return {
        "project_id": project(),
        "namespace": args.namespace,
        "kind": args.kind,
        "subject": args.subject,
        "title": args.title,
        "content": args.content,
        "tags": args.tag,
        "source_type": args.source_type,
        "source_uri": args.source_uri,
        "author_agent_id": args.author,
        "performed_by": performed_by,
        "run_id": args.run_id,
        "confidence": args.confidence,
        "visibility": "project",
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="context.py")
    commands = root.add_subparsers(dest="command", required=True)

    search = commands.add_parser("search")
    search.add_argument("query")
    search.add_argument("--kind", action="append", default=[])
    search.add_argument("--performed-by", action="append", default=[])
    search.add_argument("--limit", type=int, default=10)

    pack = commands.add_parser("pack")
    pack.add_argument("query")
    pack.add_argument("--budget", type=int, default=3000)
    pack.add_argument("--performed-by", action="append", default=[])

    put = commands.add_parser("put")
    add_memory_fields(put)

    supersede = commands.add_parser("supersede")
    supersede.add_argument("memory_id")
    add_memory_fields(supersede)

    return root


def main() -> int:
    args = parser().parse_args()
    if args.command == "search":
        result = call(
            "POST",
            "/v1/memories/search",
            {
                "project_id": project(),
                "query": args.query,
                "kinds": args.kind,
                "performed_by": args.performed_by,
                "limit": args.limit,
            },
        )
    elif args.command == "pack":
        result = call(
            "POST",
            "/v1/context-packs",
            {
                "project_id": project(),
                "query": args.query,
                "token_budget": args.budget,
                "performed_by": args.performed_by,
            },
        )
    elif args.command == "put":
        result = call("POST", "/v1/memories", memory_payload(args))
    else:
        result = call(
            "POST",
            f"/v1/memories/{args.memory_id}/supersede",
            memory_payload(args),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
