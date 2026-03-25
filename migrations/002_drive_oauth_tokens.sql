-- Run once if create_all did not create this table (existing deployments).

CREATE TABLE IF NOT EXISTS drive_oauth_tokens (
    id VARCHAR NOT NULL PRIMARY KEY,
    tenant_id VARCHAR NOT NULL,
    user_id VARCHAR NOT NULL,
    access_token TEXT NOT NULL,
    refresh_token TEXT,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    CONSTRAINT uq_drive_oauth_tenant_user UNIQUE (tenant_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_drive_oauth_tokens_tenant_id ON drive_oauth_tokens (tenant_id);
CREATE INDEX IF NOT EXISTS ix_drive_oauth_tokens_user_id ON drive_oauth_tokens (user_id);
