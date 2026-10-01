#!/usr/bin/env python3
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4


ROOT = Path(__file__).resolve().parent.parent
URL = os.getenv("CONTEXT_HUB_URL", "http://127.0.0.1:8000").rstrip("/")


def bootstrap_key() -> str:
    value = os.getenv("CONTEXT_HUB_API_KEY")
    if value:
        return value
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            if line.startswith("CONTEXT_BOOTSTRAP_API_KEY="):
                return line.split("=", 1)[1]
    raise RuntimeError("CONTEXT_HUB_API_KEY or generated .env is required")


KEY = bootstrap_key()


def request(method, path, payload=None, key=KEY, headers=None):
    request_headers = {} if key is None else {"Authorization": "Bearer " + key}
    request_headers.update(headers or {})
    data = None
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode()
    req = Request(URL + path, data=data, headers=request_headers, method=method)
    try:
        with urlopen(req, timeout=30) as response:
            body = response.read()
            return response.status, json.loads(body) if body else None
    except HTTPError as exc:
        body = exc.read()
        return exc.code, json.loads(body) if body else None


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project = "e2e-" + uuid4().hex[:12]
        cls.created = []
        cls.created_keys = []
        cls.created_sessions = []
        cls.created_spaces = []

    @classmethod
    def tearDownClass(cls):
        for session_id in cls.created_sessions:
            request("DELETE", "/v1/sessions/" + session_id)
        for space_id in cls.created_spaces:
            request("DELETE", "/v1/spaces/" + space_id)
        for memory_id in cls.created:
            request("DELETE", "/v1/memories/" + memory_id)
        for key_id in cls.created_keys:
            request("DELETE", "/v1/api-keys/" + key_id)

    def payload(self, title, content):
        return {
            "project_id": self.project,
            "namespace": "backend/approval",
            "kind": "decision",
            "subject": "payment-api/retry-policy",
            "title": title,
            "content": content,
            "tags": ["payment-api", "retry"],
            "source_type": "git",
            "source_uri": "repo://payments/commit/e2e",
            "author_agent_id": "e2e-agent",
            "performed_by": ["김민수"],
            "confidence": 0.95,
            "visibility": "project",
        }

    def test_full_memory_lifecycle_and_agent_integration(self):
        hook_spec = importlib.util.spec_from_file_location(
            "context_hub_session_end",
            ROOT / "apps/context-hooks/hooks/session_end.py",
        )
        hook_module = importlib.util.module_from_spec(hook_spec)
        hook_spec.loader.exec_module(hook_module)
        redacted = hook_module.redact(
            "Authorization: Bearer secret-value token=ctx_abcdefghijklmnop"
        )
        self.assertNotIn("secret-value", redacted)
        self.assertNotIn("ctx_abcdefghijklmnop", redacted)

        status, body = request("GET", "/readyz")
        self.assertEqual((status, body["status"]), (200, "ready"))

        status, registered = request(
            "POST", "/v1/register", {"principal": "auto-client"}, key=None
        )
        self.assertEqual(status, 201)
        self.created_keys.append(registered["id"])
        status, _ = request("GET", "/v1/spaces", key=registered["api_key"])
        self.assertEqual(status, 200)

        status, _ = request("GET", "/v1/memories/00000000-0000-0000-0000-000000000000", key="wrong-key")
        self.assertEqual(status, 401)

        issued = []
        for principal_name in ("allowed-agent", "other-agent"):
            status, key_data = request(
                "POST",
                "/v1/api-keys",
                {
                    "principal": principal_name,
                    "project_ids": [self.project],
                    "scopes": ["read"],
                },
            )
            self.assertEqual(status, 201)
            issued.append(key_data)
            self.created_keys.append(key_data["id"])

        restricted = self.payload(
            "Restricted retry evidence",
            "This retry evidence is visible only to the explicitly allowed test agent principal.",
        )
        restricted.update(
            {
                "kind": "fact",
                "subject": "payment-api/restricted-evidence",
                "visibility": "restricted",
                "allowed_principals": ["allowed-agent"],
            }
        )
        status, restricted_memory = request("POST", "/v1/memories", restricted)
        self.assertEqual(status, 201)
        self.created.append(restricted_memory["id"])
        status, _ = request(
            "GET", "/v1/memories/" + restricted_memory["id"], key=issued[0]["api_key"]
        )
        self.assertEqual(status, 200)
        status, _ = request(
            "GET", "/v1/memories/" + restricted_memory["id"], key=issued[1]["api_key"]
        )
        self.assertEqual(status, 404)
        status, _ = request(
            "POST", "/v1/memories", restricted, key=issued[0]["api_key"]
        )
        self.assertEqual(status, 403)

        first = self.payload(
            "Retry timeouts twice",
            "Retry network timeouts at most twice and require an idempotency key for every approval request.",
        )
        headers = {"Idempotency-Key": "e2e-" + uuid4().hex}
        status, created = request("POST", "/v1/memories", first, headers=headers)
        self.assertEqual(status, 201)
        self.assertEqual(created["embedding_dimensions"], 1024)
        self.assertEqual(created["performed_by"], ["김민수"])
        first_id = created["id"]
        self.created.append(first_id)

        status, replayed = request("POST", "/v1/memories", first, headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(replayed["id"], first_id)

        status, found = request(
            "POST",
            "/v1/memories/search",
            {"project_id": self.project, "query": "승인 요청 타임아웃 재시도 횟수", "limit": 5},
        )
        self.assertEqual(status, 200)
        self.assertEqual(found["items"][0]["id"], first_id)

        status, found = request(
            "POST",
            "/v1/memories/search",
            {
                "project_id": self.project,
                "query": "작업 현황",
                "performed_by": ["김민수"],
                "limit": 5,
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(found["items"][0]["performed_by"], ["김민수"])

        replacement = self.payload(
            "Retry timeouts once",
            "Retry network timeouts only once after the incident review; every approval request still requires an idempotency key.",
        )
        status, second = request(
            "POST", "/v1/memories/" + first_id + "/supersede", replacement
        )
        self.assertEqual(status, 200)
        second_id = second["id"]
        self.created.append(second_id)

        status, old = request("GET", "/v1/memories/" + first_id)
        self.assertEqual((status, old["status"]), (200, "superseded"))

        status, pack = request(
            "POST",
            "/v1/context-packs",
            {"project_id": self.project, "query": "current retry decision", "token_budget": 1000},
        )
        self.assertEqual(status, 200)
        self.assertEqual(pack["items"][0]["id"], second_id)

        env = os.environ.copy()
        env.update(
            {
                "CONTEXT_HUB_URL": URL,
                "CONTEXT_HUB_API_KEY": KEY,
                "CONTEXT_HUB_PROJECT_ID": self.project,
                "PLUGIN_ROOT": str(ROOT / "apps/context-skill"),
            }
        )
        with tempfile.TemporaryDirectory() as auto_home:
            auto_config = Path(auto_home) / "config.json"
            auto_env = {
                **env,
                "CONTEXT_HUB_CONFIG": str(auto_config),
                "CONTEXT_HUB_AGENT_ID": "context-agent-test",
            }
            auto_env.pop("CONTEXT_HUB_API_KEY")
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "apps/context-skill/scripts/context.py"),
                    "search",
                    "retry decision",
                ],
                env=auto_env,
                text=True,
                capture_output=True,
                check=True,
            )
            auto_values = json.loads(auto_config.read_text())
            self.assertEqual(auto_values["agent_id"], "context-agent-test")
            self.created_keys.append(auto_values["key_id"])
        cli = subprocess.run(
            [
                sys.executable,
                str(ROOT / "apps/context-skill/scripts/context.py"),
                "search",
                "retry decision",
            ],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        self.assertIn(second_id, cli.stdout)

        status, space = request(
            "PUT",
            "/v1/spaces/" + self.project,
            {
                "space_id": self.project,
                "display_name": "Payments test workspace",
                "description": "Payment approvals, retries, refunds, and settlement engineering work.",
                "keywords": ["payment", "approval", "retry", "refund"],
            },
        )
        self.assertEqual((status, space["space_id"]), (200, self.project))
        self.created_spaces.append(self.project)

        provider_session_id = "hook-" + uuid4().hex
        transcript = (
            '{"role":"user","content":"Investigate payment approval retry failures"}\n'
            '{"role":"assistant","content":"The payment timeout retry policy should be reviewed."}\n'
            'Authorization: Bearer sk_this-secret-must-never-be-uploaded\n'
        )
        with tempfile.TemporaryDirectory() as hook_data:
            transcript_path = Path(hook_data) / "transcript.jsonl"
            transcript_path.write_text(transcript)
            hook_env = {**env, "PLUGIN_DATA": hook_data}
            hook_env["CONTEXT_HUB_CONFIG"] = str(Path(hook_data) / "config.json")
            hook_env["CONTEXT_HUB_AGENT_ID"] = "context-agent-hook-test"
            hook_env.pop("CONTEXT_HUB_API_KEY")
            hook = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "apps/context-hooks/hooks/session_end.py"),
                    "--provider",
                    "codex",
                ],
                input=json.dumps(
                    {
                        "session_id": provider_session_id,
                        "transcript_path": str(transcript_path),
                        "cwd": str(ROOT),
                        "model": "test-model",
                        "hook_event_name": "SessionEnd",
                    }
                ),
                env=hook_env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertEqual(hook.stdout, "")
            self.assertEqual(list((Path(hook_data) / "session-outbox").glob("*.json")), [])
            hook_config = json.loads((Path(hook_data) / "config.json").read_text())
            self.assertEqual(hook_config["agent_id"], "context-agent-hook-test")
            self.created_keys.append(hook_config["key_id"])

        status, session = request(
            "POST",
            "/v1/sessions",
            {
                "provider": "codex",
                "provider_session_id": provider_session_id,
                "model": "test-model",
                "cwd": str(ROOT),
                "transcript": transcript.replace(
                    "sk_this-secret-must-never-be-uploaded", "[REDACTED_TOKEN]"
                ),
                "transcript_format": "jsonl",
                "transcript_truncated": False,
            },
        )
        self.assertEqual(status, 202)
        self.created_sessions.append(session["id"])
        for _ in range(30):
            status, classified = request("GET", "/v1/sessions/" + session["id"])
            if classified["classification_status"] not in ("pending", "processing"):
                break
            time.sleep(0.1)
        self.assertEqual(status, 200)
        self.assertEqual(classified["classification_status"], "classified")
        self.assertEqual(classified["classifications"][0]["space_id"], self.project)
        self.assertNotIn("transcript", classified)

        status, corrected = request(
            "PUT",
            "/v1/sessions/" + session["id"] + "/classification",
            {"space_ids": [self.project]},
        )
        self.assertEqual(status, 200)
        self.assertTrue(corrected["classifications"][0]["corrected"])

        status, retried = request(
            "POST", "/v1/sessions/" + session["id"] + "/retry"
        )
        self.assertEqual((status, retried["status"]), (202, "pending"))
        for _ in range(30):
            _, classified = request("GET", "/v1/sessions/" + session["id"])
            if classified["classification_status"] not in ("pending", "processing"):
                break
            time.sleep(0.1)
        self.assertEqual(classified["classification_status"], "classified")

        status, sessions = request(
            "GET", "/v1/sessions?classification_status=classified&limit=10"
        )
        self.assertEqual(status, 200)
        self.assertIn(session["id"], {item["id"] for item in sessions})
        self.assertTrue(all("transcript" not in item for item in sessions))

        status, stats = request("GET", "/v1/admin/stats")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(stats["spaces"], 1)

        claude_hooks = json.loads(
            (ROOT / "apps/context-hooks/hooks/hooks.json").read_text()
        )
        claude_handler = claude_hooks["hooks"]["SessionEnd"][0]["hooks"][0]
        self.assertEqual(claude_handler["command"], "python3")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", claude_handler["args"][0])

        codex_hooks = json.loads(
            (ROOT / "apps/context-hooks/hooks/codex-hooks.json").read_text()
        )
        codex_command = codex_hooks["hooks"]["SessionEnd"][0]["hooks"][0][
            "command"
        ]
        self.assertIn("${PLUGIN_ROOT}", codex_command)

        status, _ = request(
            "POST",
            "/v1/memories/" + second_id + "/feedback",
            {"useful": True, "note": "e2e"},
        )
        self.assertEqual(status, 204)

        status, expired = request("POST", "/v1/memories/" + second_id + "/expire")
        self.assertEqual((status, expired["status"]), (200, "expired"))

        status, found = request(
            "POST",
            "/v1/memories/search",
            {
                "project_id": self.project,
                "query": "retry decision",
                "kinds": ["decision"],
                "limit": 5,
            },
        )
        self.assertEqual((status, found["count"]), (200, 0))

        status, _ = request("DELETE", "/v1/api-keys/" + issued[0]["id"])
        self.assertEqual(status, 204)
        self.created_keys.remove(issued[0]["id"])
        status, _ = request(
            "GET", "/v1/memories/" + restricted_memory["id"], key=issued[0]["api_key"]
        )
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
