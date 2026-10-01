from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import secrets
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import Settings
from .embedding import vector_literal
from .models import MemoryCreate, SearchRequest, SessionIngest, SpaceCreate


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    name: str
    project_ids: tuple[str, ...]
    scopes: tuple[str, ...]

    def permits(self, project_id: str, scope: str) -> bool:
        return scope in self.scopes and (
            "*" in self.project_ids or project_id in self.project_ids
        )


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.pool = ConnectionPool(
            settings.database_url,
            min_size=1,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=False,
        )

    def open(self) -> None:
        self.pool.open(wait=True)
        self._migrate()
        self._bootstrap_key()
        with self.pool.connection() as connection:
            connection.execute(
                """
                UPDATE ai_session SET classification_status = 'pending'
                WHERE classification_status = 'processing'
                """
            )

    def close(self) -> None:
        self.pool.close()

    def ping(self) -> None:
        with self.pool.connection() as connection:
            connection.execute("SELECT 1").fetchone()

    def _migrate(self) -> None:
        migrations = Path(__file__).resolve().parent.parent / "migrations"
        with self.pool.connection() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migration "
                "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            for path in sorted(migrations.glob("*.sql")):
                applied = connection.execute(
                    "SELECT 1 FROM schema_migration WHERE version = %s", (path.name,)
                ).fetchone()
                if not applied:
                    connection.execute(path.read_text(), prepare=False)
                    connection.execute(
                        "INSERT INTO schema_migration(version) VALUES (%s)", (path.name,)
                    )

    def _bootstrap_key(self) -> None:
        digest = hashlib.sha256(self.settings.bootstrap_api_key.encode()).hexdigest()
        with self.pool.connection() as connection:
            connection.execute(
                """
                UPDATE api_key SET revoked_at = now()
                WHERE scopes @> ARRAY['admin'] AND key_hash <> %s
                  AND revoked_at IS NULL
                """,
                (digest,),
            )
            connection.execute(
                """
                INSERT INTO api_key(key_hash, tenant_id, principal, project_ids, scopes)
                VALUES (%s, %s, %s, %s, ARRAY['read', 'write', 'admin'])
                ON CONFLICT (key_hash) DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    principal = EXCLUDED.principal,
                    project_ids = EXCLUDED.project_ids,
                    scopes = EXCLUDED.scopes,
                    revoked_at = NULL
                """,
                (
                    digest,
                    self.settings.bootstrap_tenant,
                    self.settings.bootstrap_principal,
                    list(self.settings.bootstrap_projects),
                ),
            )

    def authenticate(self, token: str) -> Principal | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                SELECT tenant_id, principal, project_ids, scopes
                FROM api_key
                WHERE key_hash = %s AND revoked_at IS NULL
                """,
                (digest,),
            ).fetchone()
        if not row:
            return None
        return Principal(
            tenant_id=row["tenant_id"],
            name=row["principal"],
            project_ids=tuple(row["project_ids"]),
            scopes=tuple(row["scopes"]),
        )

    def create_api_key(
        self,
        owner: Principal,
        name: str,
        project_ids: list[str],
        scopes: list[str],
    ) -> dict[str, Any]:
        token = "ctx_" + secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                INSERT INTO api_key(key_hash, tenant_id, principal, project_ids, scopes)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id, tenant_id, principal, project_ids, scopes, created_at
                """,
                (digest, owner.tenant_id, name, project_ids, scopes),
            ).fetchone()
            self._audit(connection, owner, "api_key.create", None, None, [])
        return {**row, "api_key": token}

    def revoke_api_key(self, owner: Principal, key_id: UUID) -> bool:
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                UPDATE api_key SET revoked_at = now()
                WHERE id = %s AND tenant_id = %s AND revoked_at IS NULL
                RETURNING id
                """,
                (key_id, owner.tenant_id),
            ).fetchone()
            if row:
                self._audit(connection, owner, "api_key.revoke", None, None, [])
        return row is not None

    def create_space(
        self, principal: Principal, data: SpaceCreate, vector: list[float]
    ) -> dict[str, Any]:
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                INSERT INTO context_space(
                    tenant_id, space_id, display_name, description, keywords,
                    embedding, embedding_model, embedding_dimensions
                ) VALUES (%s, %s, %s, %s, %s, %s::vector, %s, %s)
                ON CONFLICT (tenant_id, space_id) DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    description = EXCLUDED.description,
                    keywords = EXCLUDED.keywords,
                    embedding = EXCLUDED.embedding,
                    embedding_model = EXCLUDED.embedding_model,
                    embedding_dimensions = EXCLUDED.embedding_dimensions
                RETURNING space_id, display_name, description, keywords, created_at
                """,
                (
                    principal.tenant_id,
                    data.space_id,
                    data.display_name,
                    data.description,
                    data.keywords,
                    vector_literal(vector),
                    self.settings.embedding_model,
                    len(vector),
                ),
            ).fetchone()
        return row

    def list_spaces(self, principal: Principal) -> list[dict[str, Any]]:
        with self.pool.connection() as connection:
            return connection.execute(
                """
                SELECT space_id, display_name, description, keywords, created_at
                FROM context_space
                WHERE tenant_id = %s AND (%s OR space_id = ANY(%s))
                ORDER BY display_name, space_id
                """,
                (
                    principal.tenant_id,
                    "*" in principal.project_ids,
                    list(principal.project_ids),
                ),
            ).fetchall()

    def delete_space(self, principal: Principal, space_id: str) -> bool:
        with self.pool.connection() as connection:
            row = connection.execute(
                "DELETE FROM context_space WHERE tenant_id = %s AND space_id = %s RETURNING space_id",
                (principal.tenant_id, space_id),
            ).fetchone()
        return row is not None

    @staticmethod
    def _session_fields(alias: str = "s") -> str:
        return f"""
            {alias}.id, {alias}.provider, {alias}.provider_session_id,
            {alias}.model, {alias}.cwd, {alias}.transcript_format,
            {alias}.transcript_truncated, {alias}.classification_status,
            {alias}.classifications, {alias}.captured_at, {alias}.updated_at
        """

    def ingest_session(self, principal: Principal, data: SessionIngest) -> dict[str, Any]:
        digest = hashlib.sha256(data.transcript.encode()).hexdigest()
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=self.settings.session_retention_days
        )
        with self.pool.connection() as connection:
            row = connection.execute(
                f"""
                INSERT INTO ai_session(
                    tenant_id, principal, provider, provider_session_id, model, cwd,
                    transcript, transcript_format, transcript_truncated, content_hash,
                    allowed_space_ids, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, provider, provider_session_id) DO UPDATE SET
                    principal = EXCLUDED.principal,
                    model = EXCLUDED.model,
                    cwd = EXCLUDED.cwd,
                    transcript = EXCLUDED.transcript,
                    transcript_format = EXCLUDED.transcript_format,
                    transcript_truncated = EXCLUDED.transcript_truncated,
                    content_hash = EXCLUDED.content_hash,
                    allowed_space_ids = EXCLUDED.allowed_space_ids,
                    expires_at = EXCLUDED.expires_at,
                    classification_status = CASE
                        WHEN ai_session.content_hash = EXCLUDED.content_hash
                        THEN ai_session.classification_status ELSE 'pending' END,
                    classifications = CASE
                        WHEN ai_session.content_hash = EXCLUDED.content_hash
                        THEN ai_session.classifications ELSE '[]'::jsonb END,
                    classification_attempts = CASE
                        WHEN ai_session.content_hash = EXCLUDED.content_hash
                        THEN ai_session.classification_attempts ELSE 0 END,
                    classification_error = CASE
                        WHEN ai_session.content_hash = EXCLUDED.content_hash
                        THEN ai_session.classification_error ELSE NULL END,
                    updated_at = now()
                RETURNING {self._session_fields('ai_session')}
                """,
                (
                    principal.tenant_id,
                    principal.name,
                    data.provider.value,
                    data.provider_session_id,
                    data.model,
                    data.cwd,
                    data.transcript,
                    data.transcript_format,
                    data.transcript_truncated,
                    digest,
                    list(principal.project_ids),
                    expires_at,
                ),
            ).fetchone()
            self._audit(connection, principal, "session.ingest", None, None, [])
        return row

    def claim_session_for_classification(self) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            with connection.transaction():
                row = connection.execute(
                    """
                    SELECT id, tenant_id, transcript, allowed_space_ids, content_hash
                    FROM ai_session
                    WHERE classification_status IN ('pending', 'failed')
                      AND classification_attempts < 5
                    ORDER BY updated_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                ).fetchone()
                if row:
                    connection.execute(
                        """
                        UPDATE ai_session
                        SET classification_status = 'processing',
                            classification_attempts = classification_attempts + 1,
                            classification_error = NULL, updated_at = now()
                        WHERE id = %s
                        """,
                        (row["id"],),
                    )
        return row

    def classify_session(
        self,
        tenant_id: str,
        allowed_space_ids: list[str],
        session_id: UUID,
        content_hash: str,
        vector: list[float],
    ) -> None:
        with self.pool.connection() as connection:
            spaces = connection.execute(
                """
                SELECT space_id, display_name,
                    GREATEST(0, 1 - (embedding <=> %s::vector)) AS score
                FROM context_space
                WHERE tenant_id = %s
                  AND embedding_model = %s AND embedding_dimensions = %s
                  AND (%s OR space_id = ANY(%s))
                ORDER BY score DESC, space_id
                LIMIT 3
                """,
                (
                    vector_literal(vector),
                    tenant_id,
                    self.settings.embedding_model,
                    len(vector),
                    "*" in allowed_space_ids,
                    allowed_space_ids,
                ),
            ).fetchall()
            classifications = [
                {
                    "space_id": row["space_id"],
                    "display_name": row["display_name"],
                    "score": round(float(row["score"]), 6),
                }
                for row in spaces
            ]
            connection.execute(
                """
                UPDATE ai_session
                SET classification_status = %s, classifications = %s::jsonb,
                    updated_at = now()
                WHERE id = %s AND tenant_id = %s AND content_hash = %s
                """,
                (
                    "classified" if classifications else "unclassified",
                    json.dumps(classifications, ensure_ascii=False),
                    session_id,
                    tenant_id,
                    content_hash,
                ),
            )

    def fail_session_classification(
        self, session_id: UUID, content_hash: str, error: str
    ) -> None:
        with self.pool.connection() as connection:
            connection.execute(
                """
                UPDATE ai_session
                SET classification_status = 'failed', classification_error = %s,
                    updated_at = now()
                WHERE id = %s AND content_hash = %s
                """,
                (error[:1000], session_id, content_hash),
            )

    def cleanup_expired_sessions(self) -> int:
        with self.pool.connection() as connection:
            return connection.execute(
                "DELETE FROM ai_session WHERE expires_at <= now()"
            ).rowcount

    def session_stats(self, principal: Principal) -> dict[str, Any]:
        with self.pool.connection() as connection:
            counts = connection.execute(
                """
                SELECT classification_status, count(*) AS count
                FROM ai_session WHERE tenant_id = %s
                GROUP BY classification_status
                """,
                (principal.tenant_id,),
            ).fetchall()
            total_spaces = connection.execute(
                "SELECT count(*) AS count FROM context_space WHERE tenant_id = %s",
                (principal.tenant_id,),
            ).fetchone()["count"]
        return {
            "sessions": {row["classification_status"]: row["count"] for row in counts},
            "spaces": total_spaces,
        }

    def retry_session_classification(
        self, principal: Principal, session_id: UUID
    ) -> bool:
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                UPDATE ai_session
                SET classification_status = 'pending', classification_attempts = 0,
                    classification_error = NULL, updated_at = now()
                WHERE id = %s AND tenant_id = %s
                RETURNING id
                """,
                (session_id, principal.tenant_id),
            ).fetchone()
        return row is not None

    def correct_session_classification(
        self, principal: Principal, session_id: UUID, space_ids: list[str]
    ) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            spaces = connection.execute(
                """
                SELECT space_id, display_name FROM context_space
                WHERE tenant_id = %s AND space_id = ANY(%s)
                """,
                (principal.tenant_id, space_ids),
            ).fetchall()
            if len(spaces) != len(space_ids):
                raise ValueError("every corrected space must exist")
            names = {row["space_id"]: row["display_name"] for row in spaces}
            classifications = [
                {
                    "space_id": space_id,
                    "display_name": names[space_id],
                    "score": 1.0,
                    "corrected": True,
                }
                for space_id in space_ids
            ]
            row = connection.execute(
                f"""
                UPDATE ai_session
                SET classification_status = %s, classifications = %s::jsonb,
                    classification_error = NULL, updated_at = now()
                WHERE id = %s AND tenant_id = %s
                RETURNING {self._session_fields('ai_session')}
                """,
                (
                    "classified" if classifications else "unclassified",
                    json.dumps(classifications, ensure_ascii=False),
                    session_id,
                    principal.tenant_id,
                ),
            ).fetchone()
        return row

    def get_session(
        self, principal: Principal, session_id: UUID
    ) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            return connection.execute(
                f"""
                SELECT {self._session_fields()}
                FROM ai_session s
                WHERE s.id = %s AND s.tenant_id = %s
                  AND (%s OR s.principal = %s)
                """,
                (session_id, principal.tenant_id, "admin" in principal.scopes, principal.name),
            ).fetchone()

    def list_sessions(
        self, principal: Principal, classification_status: str | None, limit: int
    ) -> list[dict[str, Any]]:
        with self.pool.connection() as connection:
            return connection.execute(
                f"""
                SELECT {self._session_fields()}
                FROM ai_session s
                WHERE s.tenant_id = %s
                  AND (%s OR s.principal = %s)
                  AND (%s::text IS NULL OR s.classification_status = %s)
                ORDER BY s.captured_at DESC
                LIMIT %s
                """,
                (
                    principal.tenant_id,
                    "admin" in principal.scopes,
                    principal.name,
                    classification_status,
                    classification_status,
                    limit,
                ),
            ).fetchall()

    def purge_session(self, principal: Principal, session_id: UUID) -> bool:
        with self.pool.connection() as connection:
            row = connection.execute(
                "DELETE FROM ai_session WHERE id = %s AND tenant_id = %s RETURNING id",
                (session_id, principal.tenant_id),
            ).fetchone()
        return row is not None

    @staticmethod
    def _content_hash(data: MemoryCreate) -> str:
        canonical = json.dumps(
            {
                "project_id": data.project_id,
                "namespace": data.namespace,
                "kind": data.kind.value,
                "subject": data.subject,
                "title": data.title,
                "content": data.content,
                "tags": data.tags,
                "performed_by": data.performed_by,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _query_hash(query: str) -> str:
        return hashlib.sha256(query.encode()).hexdigest()

    @staticmethod
    def _clean(row: dict[str, Any]) -> dict[str, Any]:
        cleaned = dict(row)
        cleaned.pop("embedding", None)
        cleaned.pop("freshness", None)
        for key, value in list(cleaned.items()):
            if isinstance(value, Decimal):
                cleaned[key] = float(value)
        cleaned["allowed_principals"] = cleaned.get("allowed_principals") or []
        cleaned["edges"] = cleaned.get("edges") or []
        return cleaned

    @staticmethod
    def _select_fields(alias: str = "m") -> str:
        return f"""
            {alias}.id, {alias}.tenant_id, {alias}.project_id, {alias}.namespace,
            {alias}.kind, {alias}.subject, {alias}.title, {alias}.content, {alias}.tags,
            {alias}.embedding_model, {alias}.embedding_dimensions,
            {alias}.source_type, {alias}.source_uri, {alias}.source_status,
            {alias}.evidence, {alias}.author_agent_id, {alias}.performed_by, {alias}.run_id,
            {alias}.confidence, {alias}.status, {alias}.valid_from, {alias}.expires_at,
            {alias}.visibility, {alias}.metadata, {alias}.created_at,
            ARRAY(SELECT a.principal FROM memory_acl a WHERE a.memory_id = {alias}.id)
                AS allowed_principals,
            COALESCE((
                SELECT jsonb_agg(jsonb_build_object(
                    'target_id', e.to_memory_id,
                    'relation', e.relation
                ) ORDER BY e.created_at)
                FROM memory_edge e WHERE e.from_memory_id = {alias}.id
            ), '[]'::jsonb) AS edges
        """

    def create_memory(
        self,
        principal: Principal,
        data: MemoryCreate,
        vector: list[float],
        idempotency_key: str | None,
    ) -> tuple[dict[str, Any], bool]:
        payload = data.model_dump(mode="json")
        content_hash = self._content_hash(data)
        vector_text = vector_literal(vector)
        with self.pool.connection() as connection:
            with connection.transaction():
                if data.edges:
                    target_ids = list({edge.target_id for edge in data.edges})
                    found = connection.execute(
                        """
                        SELECT count(*) AS count FROM memory
                        WHERE id = ANY(%s) AND tenant_id = %s AND project_id = %s
                        """,
                        (target_ids, principal.tenant_id, data.project_id),
                    ).fetchone()["count"]
                    if found != len(target_ids):
                        raise ValueError("every edge target must exist in the same project")

                row = connection.execute(
                    """
                    INSERT INTO memory(
                        tenant_id, project_id, namespace, kind, subject, title, content,
                        tags, embedding, embedding_model, embedding_dimensions, content_hash,
                        source_type, source_uri, evidence, author_agent_id, performed_by, run_id, confidence,
                        valid_from, expires_at, visibility, metadata, idempotency_key
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s::vector, %s, %s, %s,
                        %s, %s, %s::jsonb, %s, %s, %s, %s,
                        %s, %s, %s, %s::jsonb, %s
                    )
                    ON CONFLICT (tenant_id, project_id, idempotency_key)
                        WHERE idempotency_key IS NOT NULL
                    DO NOTHING
                    RETURNING id
                    """,
                    (
                        principal.tenant_id,
                        data.project_id,
                        data.namespace,
                        data.kind.value,
                        data.subject,
                        data.title,
                        data.content,
                        data.tags,
                        vector_text,
                        self.settings.embedding_model,
                        len(vector),
                        content_hash,
                        data.source_type.value,
                        data.source_uri,
                        json.dumps(payload["evidence"], ensure_ascii=False),
                        data.author_agent_id,
                        data.performed_by,
                        data.run_id,
                        data.confidence,
                        data.valid_from,
                        data.expires_at,
                        data.visibility.value,
                        json.dumps(data.metadata, ensure_ascii=False),
                        idempotency_key,
                    ),
                ).fetchone()
                created = row is not None
                if row:
                    memory_id = row["id"]
                    if data.visibility.value == "restricted":
                        for item in data.allowed_principals:
                            connection.execute(
                                "INSERT INTO memory_acl(memory_id, principal) VALUES (%s, %s)",
                                (memory_id, item),
                            )
                    if data.edges:
                        for edge in data.edges:
                            connection.execute(
                                """
                                INSERT INTO memory_edge(from_memory_id, to_memory_id, relation)
                                VALUES (%s, %s, %s)
                                """,
                                (memory_id, edge.target_id, edge.relation.value),
                            )
                        superseded = [
                            edge.target_id
                            for edge in data.edges
                            if edge.relation.value == "supersedes"
                        ]
                        if superseded:
                            connection.execute(
                                "UPDATE memory SET status = 'superseded' WHERE id = ANY(%s)",
                                (superseded,),
                            )
                else:
                    memory_id = connection.execute(
                        """
                        SELECT id FROM memory
                        WHERE tenant_id = %s AND project_id = %s AND idempotency_key = %s
                        """,
                        (principal.tenant_id, data.project_id, idempotency_key),
                    ).fetchone()["id"]
                self._audit(
                    connection,
                    principal,
                    "memory.create" if created else "memory.idempotent_replay",
                    data.project_id,
                    None,
                    [memory_id],
                )
        return self.get_memory(principal, memory_id), created

    def get_memory(self, principal: Principal, memory_id: UUID) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                f"""
                SELECT {self._select_fields()}
                FROM memory m
                WHERE m.id = %s AND m.tenant_id = %s
                  AND (%s OR m.project_id = ANY(%s))
                  AND (%s OR m.visibility = 'project' OR EXISTS (
                    SELECT 1 FROM memory_acl a
                    WHERE a.memory_id = m.id AND a.principal = %s
                  ))
                """,
                (
                    memory_id,
                    principal.tenant_id,
                    "*" in principal.project_ids,
                    list(principal.project_ids),
                    "admin" in principal.scopes,
                    principal.name,
                ),
            ).fetchone()
            if row:
                self._audit(
                    connection, principal, "memory.read", row["project_id"], None, [memory_id]
                )
        return self._clean(row) if row else None

    def search(
        self,
        principal: Principal,
        request: SearchRequest,
        vector: list[float],
    ) -> list[dict[str, Any]]:
        # ponytail: exact vector scan is simplest for the pilot; add a model-specific
        # HNSW index only when measured row count or p95 latency requires it.
        conditions = [
            "m.tenant_id = %s",
            "m.project_id = %s",
            "m.status = 'active'",
            "m.valid_from <= %s",
            "(m.expires_at IS NULL OR m.expires_at > %s)",
            "m.embedding_model = %s",
            "m.embedding_dimensions = %s",
            "(%s OR m.visibility = 'project' OR EXISTS (SELECT 1 FROM memory_acl a WHERE a.memory_id = m.id AND a.principal = %s))",
        ]
        params: list[Any] = [
            principal.tenant_id,
            request.project_id,
            request.as_of,
            request.as_of,
            self.settings.embedding_model,
            len(vector),
            "admin" in principal.scopes,
            principal.name,
        ]
        if request.namespace:
            conditions.append("m.namespace = %s")
            params.append(request.namespace)
        if request.kinds:
            conditions.append("m.kind = ANY(%s)")
            params.append([kind.value for kind in request.kinds])
        if request.tags:
            conditions.append("m.tags @> %s")
            params.append([tag.strip().lower() for tag in request.tags])
        if request.performed_by:
            conditions.append("m.performed_by && %s")
            params.append([name.strip() for name in request.performed_by if name.strip()])

        params.extend(
            [
                vector_literal(vector),
                request.query.lower(),
                request.as_of,
                request.limit,
            ]
        )
        with self.pool.connection() as connection:
            rows = connection.execute(
                f"""
                WITH scoped AS MATERIALIZED (
                    SELECT m.* FROM memory m WHERE {' AND '.join(conditions)}
                ), ranked AS (
                    SELECT {self._select_fields('m')},
                        GREATEST(0, 1 - (m.embedding <=> %s::vector)) AS vector_score,
                        similarity(
                            lower(m.subject || ' ' || m.title || ' ' || m.content || ' ' || array_to_string(m.tags, ' ') || ' ' || array_to_string(m.performed_by, ' ')),
                            %s
                        ) AS text_score,
                        1.0 / (1.0 + GREATEST(0, extract(epoch FROM (%s - m.created_at))) / 2592000.0) AS freshness
                    FROM scoped m
                )
                SELECT *,
                    0.65 * vector_score + 0.20 * text_score +
                    0.10 * freshness + 0.05 * confidence AS score
                FROM ranked
                ORDER BY score DESC, created_at DESC
                LIMIT %s
                """,
                params,
            ).fetchall()
            memory_ids = [row["id"] for row in rows]
            self._audit(
                connection,
                principal,
                "memory.search",
                request.project_id,
                self._query_hash(request.query),
                memory_ids,
            )
        return [self._clean(row) for row in rows]

    def expire(self, principal: Principal, memory_id: UUID) -> dict[str, Any] | None:
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                UPDATE memory SET status = 'expired'
                WHERE id = %s AND tenant_id = %s
                  AND (%s OR project_id = ANY(%s))
                RETURNING project_id
                """,
                (
                    memory_id,
                    principal.tenant_id,
                    "*" in principal.project_ids,
                    list(principal.project_ids),
                ),
            ).fetchone()
            if row:
                self._audit(connection, principal, "memory.expire", row["project_id"], None, [memory_id])
        return self.get_memory(principal, memory_id) if row else None

    def feedback(
        self, principal: Principal, memory_id: UUID, useful: bool, note: str | None
    ) -> bool:
        memory = self.get_memory(principal, memory_id)
        if not memory:
            return False
        with self.pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO memory_feedback(memory_id, tenant_id, principal, useful, note)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (memory_id, principal.tenant_id, principal.name, useful, note),
            )
        return True

    def purge(self, principal: Principal, memory_id: UUID) -> bool:
        with self.pool.connection() as connection:
            row = connection.execute(
                """
                DELETE FROM memory WHERE id = %s AND tenant_id = %s
                  AND (%s OR project_id = ANY(%s))
                RETURNING project_id
                """,
                (
                    memory_id,
                    principal.tenant_id,
                    "*" in principal.project_ids,
                    list(principal.project_ids),
                ),
            ).fetchone()
            if row:
                self._audit(connection, principal, "memory.purge", row["project_id"], None, [memory_id])
        return row is not None

    @staticmethod
    def _audit(
        connection: Any,
        principal: Principal,
        action: str,
        project_id: str | None,
        query_hash: str | None,
        memory_ids: list[UUID],
    ) -> None:
        connection.execute(
            """
            INSERT INTO memory_access_log(
                tenant_id, principal, action, project_id, query_hash, memory_ids
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                principal.tenant_id,
                principal.name,
                action,
                project_id,
                query_hash,
                memory_ids,
            ),
        )
