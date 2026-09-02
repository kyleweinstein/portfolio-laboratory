-- Sparse Webull v1 imports used opening/closing statements plus the cash-transfer
-- ledger. That ledger does not prove coverage of separate incoming or outgoing
-- security transfers, so those batches cannot support reconciled performance.
-- Full contiguous monthly statement imports remain eligible after re-import.
UPDATE statement_import_batches batch
SET publication_eligible = FALSE
WHERE batch.publication_eligible = TRUE
  AND batch.contiguous_monthly_coverage = FALSE
  AND EXISTS (
      SELECT 1
      FROM statement_import_batch_sources batch_source
      JOIN statement_import_sources source
        ON source.source_id = batch_source.source_id
      WHERE batch_source.batch_id = batch.batch_id
        AND source.provider = 'webull'
  );
