CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS schema_migration (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS api_key (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash char(64) UNIQUE NOT NULL,
    tenant_id text NOT NULL,
    principal text NOT NULL,
    project_ids text[] NOT NULL,
    scopes text[] NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);

CREATE TABLE IF NOT EXISTS memory (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text NOT NULL,
    project_id text NOT NULL,
    namespace text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('fact', 'decision', 'artifact', 'handoff', 'failure')),
    subject text NOT NULL,
    title text NOT NULL,
    content text NOT NULL,
    tags text[] NOT NULL DEFAULT '{}',
    embedding vector NOT NULL,
    embedding_model text NOT NULL,
    embedding_dimensions integer NOT NULL CHECK (embedding_dimensions > 0),
    content_hash char(64) NOT NULL,
    source_type text NOT NULL CHECK (source_type IN ('agent_run', 'git', 'issue', 'document', 'human')),
    source_uri text NOT NULL,
    source_status text NOT NULL DEFAULT 'available' CHECK (source_status IN ('available', 'unavailable')),
    evidence jsonb NOT NULL DEFAULT '[]',
    author_agent_id text NOT NULL,
    run_id text,
    confidence double precision NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded', 'expired')),
    valid_from timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz,
    visibility text NOT NULL DEFAULT 'project' CHECK (visibility IN ('project', 'restricted')),
    metadata jsonb NOT NULL DEFAULT '{}',
    idempotency_key text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (expires_at IS NULL OR expires_at > valid_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS memory_idempotency_idx
    ON memory (tenant_id, project_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS memory_scope_idx
    ON memory (tenant_id, project_id, status, kind, namespace);
CREATE INDEX IF NOT EXISTS memory_subject_idx
    ON memory (tenant_id, project_id, subject);

CREATE TABLE IF NOT EXISTS memory_acl (
    memory_id uuid NOT NULL REFERENCES memory(id) ON DELETE CASCADE,
    principal text NOT NULL,
    PRIMARY KEY (memory_id, principal)
);

CREATE TABLE IF NOT EXISTS memory_edge (
    from_memory_id uuid NOT NULL REFERENCES memory(id) ON DELETE CASCADE,
    to_memory_id uuid NOT NULL REFERENCES memory(id) ON DELETE CASCADE,
    relation text NOT NULL CHECK (relation IN ('supersedes', 'derived_from', 'supports', 'blocks', 'related_to')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (from_memory_id, to_memory_id, relation),
    CHECK (from_memory_id <> to_memory_id)
);

CREATE TABLE IF NOT EXISTS memory_access_log (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id text NOT NULL,
    principal text NOT NULL,
    action text NOT NULL,
    project_id text,
    query_hash char(64),
    memory_ids uuid[] NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memory_feedback (
    memory_id uuid NOT NULL REFERENCES memory(id) ON DELETE CASCADE,
    tenant_id text NOT NULL,
    principal text NOT NULL,
    useful boolean NOT NULL,
    note text,
    created_at timestamptz NOT NULL DEFAULT now()
);
