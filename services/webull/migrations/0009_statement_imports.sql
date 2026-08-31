-- Private, normalized statement-import storage. Raw PDFs, extracted text, and
-- encrypted upload bytes are intentionally never persisted.
ALTER TABLE broker_connections
    ADD COLUMN IF NOT EXISTS owner_github_id VARCHAR(32);

ALTER TABLE broker_connections
    DROP CONSTRAINT IF EXISTS broker_connections_owner_github_id_check;
ALTER TABLE broker_connections
    ADD CONSTRAINT broker_connections_owner_github_id_check CHECK (
        owner_github_id IS NULL OR owner_github_id ~ '^[1-9][0-9]{0,31}$'
    );

ALTER TABLE brokerage_accounts
    ADD COLUMN IF NOT EXISTS owner_github_id VARCHAR(32);
ALTER TABLE brokerage_accounts
    ADD COLUMN IF NOT EXISTS account_handle VARCHAR(64) COLLATE "C";

-- Existing public-management handles were the internal UUID rendered as text.
-- Preserve that opaque value without interpreting an incoming handle as a UUID.
UPDATE brokerage_accounts
SET account_handle = internal_account_id::TEXT
WHERE account_handle IS NULL;

ALTER TABLE brokerage_accounts
    ALTER COLUMN account_handle SET NOT NULL;
ALTER TABLE brokerage_accounts
    DROP CONSTRAINT IF EXISTS brokerage_accounts_owner_github_id_check;
ALTER TABLE brokerage_accounts
    ADD CONSTRAINT brokerage_accounts_owner_github_id_check CHECK (
        owner_github_id IS NULL OR owner_github_id ~ '^[1-9][0-9]{0,31}$'
    );
ALTER TABLE brokerage_accounts
    DROP CONSTRAINT IF EXISTS brokerage_accounts_account_handle_check;
ALTER TABLE brokerage_accounts
    ADD CONSTRAINT brokerage_accounts_account_handle_check CHECK (
        account_handle ~ '^[A-Za-z0-9][A-Za-z0-9_-]{6,62}[A-Za-z0-9]$'
        AND account_handle !~ '^[0-9]+$'
    );

CREATE UNIQUE INDEX IF NOT EXISTS brokerage_accounts_owner_handle_idx
    ON brokerage_accounts (owner_github_id, account_handle)
    WHERE owner_github_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS broker_connections_owner_provider_idx
    ON broker_connections (owner_github_id, provider, status);

CREATE TABLE IF NOT EXISTS statement_import_batches (
    batch_id UUID PRIMARY KEY,
    internal_account_id UUID NOT NULL
        REFERENCES brokerage_accounts(internal_account_id) ON DELETE CASCADE,
    owner_github_id VARCHAR(32) NOT NULL
        CHECK (owner_github_id ~ '^[1-9][0-9]{0,31}$'),
    account_handle VARCHAR(64) COLLATE "C" NOT NULL,
    payload_sha256 CHAR(64) NOT NULL
        CHECK (payload_sha256 ~ '^[a-f0-9]{64}$'),
    source_document_count INTEGER NOT NULL CHECK (source_document_count > 0),
    anchor_count INTEGER NOT NULL CHECK (anchor_count > 0),
    external_flow_count INTEGER NOT NULL CHECK (external_flow_count >= 0),
    coverage_start DATE NOT NULL,
    coverage_end DATE NOT NULL,
    contiguous_monthly_coverage BOOLEAN NOT NULL,
    publication_eligible BOOLEAN NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (coverage_start <= coverage_end)
);

CREATE INDEX IF NOT EXISTS statement_import_batches_account_coverage_idx
    ON statement_import_batches (
        internal_account_id, coverage_start, coverage_end
    );

CREATE TABLE IF NOT EXISTS statement_import_sources (
    source_id VARCHAR(71) PRIMARY KEY
        CHECK (source_id ~ '^sha256:[a-f0-9]{64}$'),
    internal_account_id UUID NOT NULL
        REFERENCES brokerage_accounts(internal_account_id) ON DELETE CASCADE,
    source_content_sha256 CHAR(64) NOT NULL
        CHECK (source_content_sha256 ~ '^[a-f0-9]{64}$'),
    provider TEXT NOT NULL CHECK (provider IN ('m1', 'apex')),
    parser_version VARCHAR(80) NOT NULL,
    page_count INTEGER NOT NULL CHECK (page_count > 0),
    statement_start DATE NOT NULL,
    statement_end DATE NOT NULL,
    account_fingerprint VARCHAR(76) NOT NULL
        CHECK (account_fingerprint ~ '^hmac-sha256:[a-f0-9]{64}$'),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (statement_start <= statement_end)
);

CREATE TABLE IF NOT EXISTS statement_import_batch_sources (
    batch_id UUID NOT NULL
        REFERENCES statement_import_batches(batch_id) ON DELETE CASCADE,
    source_id VARCHAR(71) NOT NULL
        REFERENCES statement_import_sources(source_id) ON DELETE RESTRICT,
    PRIMARY KEY (batch_id, source_id)
);

ALTER TABLE statement_anchors
    ADD COLUMN IF NOT EXISTS statement_import_batch_id UUID
        REFERENCES statement_import_batches(batch_id) ON DELETE CASCADE;
ALTER TABLE statement_anchors
    ADD COLUMN IF NOT EXISTS statement_source_id VARCHAR(71)
        REFERENCES statement_import_sources(source_id) ON DELETE RESTRICT;
ALTER TABLE statement_anchors
    ADD COLUMN IF NOT EXISTS cash_balance NUMERIC(38, 12);
ALTER TABLE statement_anchors
    ADD COLUMN IF NOT EXISTS margin_debit_balance NUMERIC(38, 12);
ALTER TABLE statement_anchors
    ADD COLUMN IF NOT EXISTS signed_cash_balance NUMERIC(38, 12);
ALTER TABLE statement_anchors
    ADD COLUMN IF NOT EXISTS quality TEXT;

ALTER TABLE statement_anchors
    DROP CONSTRAINT IF EXISTS statement_anchors_import_quality_check;
ALTER TABLE statement_anchors
    ADD CONSTRAINT statement_anchors_import_quality_check CHECK (
        quality IS NULL OR quality = 'statement_reconciled'
    );
CREATE UNIQUE INDEX IF NOT EXISTS statement_anchors_import_source_idx
    ON statement_anchors (internal_account_id, statement_source_id)
    WHERE statement_source_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS statement_external_flows (
    internal_account_id UUID NOT NULL
        REFERENCES brokerage_accounts(internal_account_id) ON DELETE CASCADE,
    batch_id UUID NOT NULL
        REFERENCES statement_import_batches(batch_id) ON DELETE CASCADE,
    source_id VARCHAR(71) NOT NULL
        REFERENCES statement_import_sources(source_id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    flow_date DATE NOT NULL,
    kind TEXT NOT NULL CHECK (
        kind IN (
            'deposit', 'withdrawal', 'security_transfer_in',
            'security_transfer_out', 'security_transfer_unknown'
        )
    ),
    amount NUMERIC(38, 12),
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    valuation_status TEXT NOT NULL CHECK (
        valuation_status IN ('reported', 'unavailable')
    ),
    evidence TEXT NOT NULL CHECK (evidence = 'statement_activity'),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (internal_account_id, source_id, sequence),
    CHECK (
        (valuation_status = 'reported' AND amount IS NOT NULL)
        OR (valuation_status = 'unavailable' AND amount IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS statement_external_flows_account_date_idx
    ON statement_external_flows (internal_account_id, flow_date);

REVOKE ALL ON statement_import_batches FROM PUBLIC;
REVOKE ALL ON statement_import_sources FROM PUBLIC;
REVOKE ALL ON statement_import_batch_sources FROM PUBLIC;
REVOKE ALL ON statement_external_flows FROM PUBLIC;

COMMENT ON TABLE statement_import_batches IS
    'Amount-free private import receipts and payload hashes; no raw document data';
COMMENT ON TABLE statement_import_sources IS
    'Normalized statement source metadata and semantic hashes only';
COMMENT ON TABLE statement_external_flows IS
    'Private normalized external flows; excluded from follower projections';
