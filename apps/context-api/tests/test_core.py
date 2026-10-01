from datetime import datetime, timedelta, timezone
import unittest

from pydantic import ValidationError

from context_api.db import Principal
from context_api.embedding import node_text, session_text, vector_literal
from context_api.models import ApiKeyCreate, MemoryCreate, SpaceCreate


def memory(**overrides):
    values = {
        "project_id": "payments",
        "namespace": "backend/approval",
        "kind": "decision",
        "subject": "payment-api/retry-policy",
        "title": "Approval retry policy",
        "content": "Retry network timeouts twice and require an idempotency key.",
        "tags": [" Retry ", "retry", "Payment"],
        "source_type": "git",
        "source_uri": "repo://payments/commit/abc123",
        "author_agent_id": "test-agent",
        "performed_by": ["김민수"],
    }
    values.update(overrides)
    return MemoryCreate(**values)


class ModelTests(unittest.TestCase):
    def test_normalizes_and_deduplicates_tags(self):
        self.assertEqual(memory().tags, ["payment", "retry"])

    def test_restricted_memory_requires_acl(self):
        with self.assertRaises(ValidationError):
            memory(visibility="restricted")

    def test_expiry_must_follow_validity(self):
        now = datetime.now(timezone.utc)
        with self.assertRaises(ValidationError):
            memory(valid_from=now, expires_at=now - timedelta(seconds=1))

    def test_embedding_text_excludes_scope_and_acl(self):
        text = node_text(
            "decision", "retry-policy", "Retry", "Twice", ["api"], ["김민수"]
        )
        self.assertIn("subject: retry-policy", text)
        self.assertIn("performed by: 김민수", text)
        self.assertNotIn("tenant", text)
        self.assertNotIn("principal", text)

    def test_vector_literal(self):
        self.assertEqual(vector_literal([0.25, -0.5]), "[0.25,-0.5]")

    def test_principal_project_and_scope(self):
        principal = Principal("tenant", "agent", ("payments",), ("read",))
        self.assertTrue(principal.permits("payments", "read"))
        self.assertFalse(principal.permits("other", "read"))
        self.assertFalse(principal.permits("payments", "write"))

    def test_issued_key_cannot_be_admin(self):
        with self.assertRaises(ValidationError):
            ApiKeyCreate(
                principal="nested-admin",
                project_ids=["payments"],
                scopes=["admin"],
            )

    def test_space_keywords_are_normalized(self):
        space = SpaceCreate(
            space_id="payments",
            display_name="Payments",
            description="Payment approval and refund operations.",
            keywords=[" Refund ", "refund", "Approval"],
        )
        self.assertEqual(space.keywords, ["approval", "refund"])

    def test_session_classification_text_is_bounded(self):
        transcript = "head" + ("x" * 20_000) + "tail"
        result = session_text(transcript)
        self.assertLessEqual(len(result), 16_100)
        self.assertTrue(result.startswith("head"))
        self.assertTrue(result.endswith("tail"))


if __name__ == "__main__":
    unittest.main()
