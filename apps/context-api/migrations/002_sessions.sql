CREATE TABLE IF NOT EXISTS context_space (
    tenant_id text NOT NULL,
    space_id text NOT NULL,
    display_name text NOT NULL,
    description text NOT NULL,
    keywords text[] NOT NULL DEFAULT '{}',
    embedding vector NOT NULL,
    embedding_model text NOT NULL,
    embedding_dimensions integer NOT NULL CHECK (embedding_dimensions > 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, space_id)
);

CREATE TABLE IF NOT EXISTS ai_session (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text NOT NULL,
    principal text NOT NULL,
    provider text NOT NULL CHECK (provider IN ('codex', 'claude')),
    provider_session_id text NOT NULL,
    model text,
    cwd text,
    transcript text NOT NULL,
    transcript_format text NOT NULL DEFAULT 'jsonl',
    transcript_truncated boolean NOT NULL DEFAULT false,
    content_hash char(64) NOT NULL,
    classification_status text NOT NULL DEFAULT 'pending'
        CHECK (classification_status IN ('pending', 'classified', 'unclassified', 'failed')),
    classifications jsonb NOT NULL DEFAULT '[]',
    captured_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, provider, provider_session_id)
);

CREATE INDEX IF NOT EXISTS ai_session_classification_idx
    ON ai_session (tenant_id, classification_status, captured_at DESC);
