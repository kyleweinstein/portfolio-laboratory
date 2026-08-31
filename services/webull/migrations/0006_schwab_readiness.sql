-- Provider-neutral OAuth credential envelope used by future read-only broker
-- adapters. All token material remains encrypted with the Railway-held
-- BROKER_CREDENTIAL_ENCRYPTION_KEY; public projections never join this table.
ALTER TABLE broker_connection_credentials
    ADD COLUMN IF NOT EXISTS encrypted_refresh_token BYTEA,
    ADD COLUMN IF NOT EXISTS access_token_expires_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS refresh_token_expires_at TIMESTAMPTZ;

REVOKE ALL ON broker_connection_credentials FROM PUBLIC;

COMMENT ON COLUMN broker_connection_credentials.encrypted_refresh_token IS
    'OAuth refresh token encrypted with pgcrypto; never returned by an API';
COMMENT ON COLUMN broker_connection_credentials.access_token_expires_at IS
    'Authoritative provider expiry for the encrypted access token';
COMMENT ON COLUMN broker_connection_credentials.refresh_token_expires_at IS
    'Authoritative provider expiry for the encrypted refresh token when supplied';
