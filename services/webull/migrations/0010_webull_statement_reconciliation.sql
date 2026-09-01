-- Provider-neutral statement reconciliation expansion. No raw document bytes,
-- account values, or transfer amounts are introduced by this migration.
ALTER TABLE statement_import_sources
    DROP CONSTRAINT IF EXISTS statement_import_sources_provider_check;
ALTER TABLE statement_import_sources
    ADD CONSTRAINT statement_import_sources_provider_check
    CHECK (provider IN ('m1', 'apex', 'webull'));

ALTER TABLE statement_import_sources
    ADD COLUMN IF NOT EXISTS source_kind TEXT NOT NULL DEFAULT 'statement';
ALTER TABLE statement_import_sources
    DROP CONSTRAINT IF EXISTS statement_import_sources_source_kind_check;
ALTER TABLE statement_import_sources
    ADD CONSTRAINT statement_import_sources_source_kind_check
    CHECK (source_kind IN ('statement', 'activity_ledger'));
ALTER TABLE statement_import_sources
    DROP CONSTRAINT IF EXISTS statement_import_sources_page_count_check;
ALTER TABLE statement_import_sources
    ADD CONSTRAINT statement_import_sources_page_count_check CHECK (
        (source_kind = 'statement' AND page_count > 0)
        OR (source_kind = 'activity_ledger' AND page_count = 0)
    );

ALTER TABLE statement_import_batches
    ADD COLUMN IF NOT EXISTS external_flow_coverage_complete BOOLEAN
        NOT NULL DEFAULT FALSE;

ALTER TABLE statement_external_flows
    ADD COLUMN IF NOT EXISTS flow_at TIMESTAMPTZ;
ALTER TABLE statement_external_flows
    DROP CONSTRAINT IF EXISTS statement_external_flows_evidence_check;
ALTER TABLE statement_external_flows
    ADD CONSTRAINT statement_external_flows_evidence_check
    CHECK (evidence IN ('statement_activity', 'broker_activity_ledger'));

-- Existing Webull connections predate owner scoping. Bind the connection only
-- when all associated accounts agree on one configured owner.
WITH webull_owner AS (
    SELECT connection_id, MIN(owner_github_id) AS owner_github_id
    FROM brokerage_accounts
    WHERE provider = 'webull'
      AND connection_id IS NOT NULL
      AND owner_github_id IS NOT NULL
    GROUP BY connection_id
    HAVING COUNT(DISTINCT owner_github_id) = 1
)
UPDATE broker_connections connection
SET owner_github_id = owner.owner_github_id,
    updated_at = NOW()
FROM webull_owner owner
WHERE connection.connection_id = owner.connection_id
  AND connection.provider = 'webull'
  AND connection.owner_github_id IS NULL;
