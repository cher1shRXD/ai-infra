ALTER TABLE memory
    ADD COLUMN IF NOT EXISTS performed_by text[] NOT NULL DEFAULT '{}';
