# M1/Apex statement backfill tool

This one-time operator tool turns already-downloaded M1 Finance or Apex Clearing PDF statements into a normalized, private Portfolio Lab import bundle. It extracts statement-ending account anchors, signed cash/margin evidence, and external-flow evidence. It never downloads statements, connects to M1, or writes to the Portfolio Lab database.

## Security boundary

- Put real PDFs and every output directory **outside all Git worktrees**. The tool rejects inputs, reconciliation files, output directories, and key files beneath a `.git` directory.
- Console output contains only event codes, opaque batch IDs, counts, readiness flags, and reconciliation status. It never prints filenames, statement text, account handles, account identifiers, balances, or transaction amounts.
- Source filenames are not retained. Each source is identified by a SHA-256 digest; exact duplicate PDFs are skipped.
- Only the last four unmasked account characters are used for cross-layout matching, and they immediately become a keyed HMAC fingerprint. Neither those characters nor the brokerage account number enter output. The private bundle uses the owner-visible, opaque Portfolio Lab `accountHandle` supplied with `--account-handle`; it never accepts or emits an internal database ID or broker account number.
- Private mode applies owner-only POSIX permissions (`0700` directories and `0600` files). On a shared Windows machine, use encrypted mode because POSIX mode bits alone are not an ACL guarantee.
- Raw PDFs are never copied. Delete downloaded local PDFs after the encrypted bundle has been imported and independently verified; the originals remain available from M1.
- Do not attach real statements or normalized output to GitHub issues, pull requests, chat, or logs.

## Installation

From this directory, create a dedicated environment and install the tool:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

For development and synthetic PDF tests, install `requirements-dev.txt` instead.

## Encrypted import bundle (recommended)

Create a Fernet key outside Git without printing it:

```powershell
.\.venv\Scripts\python -m m1_statements create-key "$env:LOCALAPPDATA\PortfolioLab\m1-output.key"
$env:M1_STATEMENT_OUTPUT_KEY = (Get-Content -Raw "$env:LOCALAPPDATA\PortfolioLab\m1-output.key").Trim()
```

Ingest a private download directory. Copy `--account-handle` exactly from the selected account in the owner-only Manage experience. Treat it as a case-sensitive opaque token even if its current shape resembles a UUID; never substitute an M1/Apex account number or suffix:

```powershell
.\.venv\Scripts\python -m m1_statements ingest "D:\Private\M1\Statements" `
  --output-dir "D:\Private\PortfolioLab\Backfill" `
  --account-handle "plh_example_owner_handle_1234" `
  --mode encrypted `
  --expected-start 2020-01-01 `
  --expected-end 2026-08-31 `
  --reconcile "D:\Private\PortfolioLab\broker-reconciliation.json"
```

Encrypted mode writes one `*.json.fernet` artifact. The Fernet token decrypts to the canonical JSON bundle described by `schemas/backfill-bundle.schema.json`. Keep the key in Railway variables or another secret manager; never commit it.

## Local-private JSON and CSV

For a single-user private machine, pass `--mode private`. The batch directory contains:

- `normalized.private.json` - canonical backend import bundle
- `sources.private.csv` - source hashes and statement coverage
- `anchors.private.csv` - ending values and signed cash/margin anchors
- `external-flows.private.csv` - dated deposits, withdrawals, and security-transfer evidence
- `validation.private.json` - amount-free validation and reconciliation report

The output root also contains `.fingerprint.key`. Preserve it between batches so the same statement account receives the same HMAC fingerprint. Never move these files into this repository.

## Extraction and validation behavior

- Text-based M1 and Apex PDFs are supported. Locally OCR scanned/image-only PDFs before ingestion; the tool does not upload documents to an OCR service.
- Current Apex statements with a bare first-page date range and an `OPENING BALANCE` / `CLOSING BALANCE` table are supported. For two-column `Cash account` and `TOTAL PRICED PORTFOLIO` rows, the parser uses the closing column only.
- Every statement must expose its issuer, statement period, account identifier, USD ending account value, and an extractable text layer.
- Ending cash and margin debit are optional extraction fields. When both appear, signed cash is `cash - margin debit`; a margin-only statement therefore produces negative signed cash.
- Only deposits, withdrawals, contributions/distributions, wires, and explicitly directional transfers are treated as external flows. Buys, sells, dividends, interest, fees, taxes, and margin interest are intentionally excluded from external flows.
- Ambiguous transfers and unvalued transfer activity are blocking validation issues. Missing cash is a warning because current broker snapshots may still supply it.
- Missing monthly statements block import unless `--allow-gaps` is explicitly used. `--expected-start` and `--expected-end` enforce outer coverage boundaries.
- A PDF with the same SHA-256 digest is ignored. Two different PDFs that report conflicting anchors for the same ending date block import.

## Private reconciliation input

The optional `--reconcile` file follows `schemas/reconciliation-input.schema.json`. It is a private export from the broker/Plaid normalization layer and contains the exact same `accountHandle`, same-date account snapshots, and normalized external flows. Account-anchor matching uses one-cent absolute tolerance or one-part-per-million relative tolerance, whichever is larger; normalized external flows match on date, kind, and exact cents.

The report contains only matched/mismatched counts, dates, and fixed issue codes. It never echoes compared amounts. `publicationReady` is true only when statement validation and reconciliation both pass; a bundle can be `importReady` while still awaiting reconciliation.

## Verified cleanup

Cleanup is deliberately separate from extraction and is a dry run by default. It hashes every PDF again, requires every hash to appear in a publication-ready bundle, and refuses to remove anything if the directory contains an unverified PDF:

```powershell
.\.venv\Scripts\python -m m1_statements cleanup "D:\Private\M1\Statements" `
  --bundle "D:\Private\PortfolioLab\Backfill\m1-backfill-BATCH.json.fernet"

# After reviewing the verified PDF count:
.\.venv\Scripts\python -m m1_statements cleanup "D:\Private\M1\Statements" `
  --bundle "D:\Private\PortfolioLab\Backfill\m1-backfill-BATCH.json.fernet" `
  --confirm-delete
```

Encrypted cleanup uses `M1_STATEMENT_OUTPUT_KEY`. The command removes PDF files only; it leaves the private input directory and unrelated files intact.

## Live statement validation required

The current Apex opening/closing layout has been validated locally against a private source statement without retaining the PDF or extracted values in the repository. Real issuers can still change headings, table extraction order, transaction wording, masked-account formatting, and PDF text encoding. Before production backfill, validate a private copy of at least one real statement from every other observed layout/year against the visible statement totals and activity. Add a new sanitized parser rule and synthetic regression fixture for each confirmed variation. Do not enable cleanup or backend publication until real-statement anchors, signed cash/margin, account fingerprints, and external-flow classification reconcile exactly.

## Backend integration contract

The private broker service must implement this versioned import boundary. It is an operator/Railway job boundary, not a viewer API or browser upload endpoint.

1. Accept decrypted canonical JSON only after decrypting a `*.json.fernet` artifact inside the private service with `M1_STATEMENT_OUTPUT_KEY`. Require `schemaVersion = portfolio-lab.m1-statement-backfill.v2`; reject v1 instead of guessing that `accountRef` means `accountHandle`.
2. Validate the full bundle against `schemas/backfill-bundle.schema.json`. Require one exact, case-sensitive `accountHandle` at the bundle root and require every anchor and external-flow row to contain the identical handle. The accepted tool-side syntax is 8-64 ASCII letters, digits, `_`, or `-`, beginning and ending with a letter or digit and not consisting only of digits.
3. Resolve `accountHandle` through the owner-scoped provider-account lookup to exactly one private `plaid_m1` account and only then obtain the internal account UUID. Never parse the handle as a UUID or use it as a broker account ID. Reject unknown, stale, cross-owner, cross-provider, or multiply resolved handles.
4. Independently verify the SHA-256 `sourceId` format, consistent keyed `accountFingerprint`, USD currency, unique `(sourceId, date)` anchors, unique `(sourceId, sequence)` flows, dates inside declared coverage, exact-decimal monetary strings, validation counts, and `validation.importReady`. Treat `signedCashBalance` as private reconciliation evidence, never as a follower dollar field.
5. In one database transaction, upsert source hashes idempotently, insert statement anchors and external flows, record the batch receipt, and recompute coverage. A duplicate `batchId` with identical content returns the existing receipt; a reused `batchId` or `sourceId` with different content is a blocking integrity error. Any failure rolls back the whole batch and preserves the prior successful snapshot/publication.
6. Return only an amount-free receipt: `{batchId, accountHandle, sourceDocumentCount, anchorCount, externalFlowCount, coverageStart, coverageEnd, importStatus, publicationEligible}`. Do not return anchors, flows, balances, filenames, account identifiers, or extracted text to the browser or logs.
7. Use statement anchors and external flows for statement-reconciled Modified Dietz/TWR backfill. Do not interpolate daily account values between monthly statements. Publication remains withheld until the service independently confirms ownership, complete flow coverage, signed-allocation reconciliation, and `validation.publicationReady = true`.
8. Persist only normalized private records and source hashes; do not persist raw PDF bytes or unrestricted extracted text. The follower projection may copy only signed weights, percentage performance, and permitted average cost per share, never anchor values, cash dollar amounts, quantities, account identifiers, or raw activities.

The companion reconciliation export is also versioned: require `schemaVersion = portfolio-lab.broker-reconciliation.v2` and the identical top-level `accountHandle` before the tool marks a bundle publication-ready.

## Tests

Tests create synthetic M1/Apex PDFs in the operating-system temporary directory. No real statement or account data is included:

```powershell
.\.venv\Scripts\python -m pytest
```
