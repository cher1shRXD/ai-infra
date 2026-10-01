ALTER TABLE ai_session
    DROP CONSTRAINT IF EXISTS ai_session_classification_status_check;

ALTER TABLE ai_session
    ADD COLUMN IF NOT EXISTS allowed_space_ids text[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS classification_attempts integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS classification_error text,
    ADD COLUMN IF NOT EXISTS expires_at timestamptz;

ALTER TABLE ai_session
    ADD CONSTRAINT ai_session_classification_status_check
    CHECK (classification_status IN ('pending', 'processing', 'classified', 'unclassified', 'failed'));

UPDATE ai_session SET expires_at = captured_at + interval '90 days'
WHERE expires_at IS NULL;

CREATE INDEX IF NOT EXISTS ai_session_retention_idx ON ai_session (expires_at);
