-- Fetch patent records by publication number from Google Patents Public Data.
-- Dataset: `patents-public-data.patents.publications` (BigQuery)
--
-- HOW TO USE (zero-install path):
--   1. Open https://console.cloud.google.com/bigquery (sign in with your Google
--      account — BigQuery Sandbox, no credit card needed).
--   2. Replace @numbers below with your canonical numbers, run, then
--      "Save results" -> "JSON". Point BigQueryExportSource at that file.
--
-- COST NOTE: full-text columns (claims_localized) are large, and filtering by
-- publication_number does NOT reduce bytes scanned (the table is not clustered
-- on it) — each run scans the selected columns across the whole table. Keep the
-- column list minimal. Sandbox free tier = 1 TB processed/month.
--
-- The canonical numbers produced by patentkit.normalize (e.g. "US-10123456-B2")
-- match this table's `publication_number` format directly.

DECLARE numbers ARRAY<STRING> DEFAULT ["US-10123456-B2", "EP-1234567-B1"];

SELECT
  publication_number,
  country_code,
  kind_code,
  application_number,
  publication_date,
  filing_date,
  grant_date,
  family_id,
  title_localized,
  abstract_localized,
  claims_localized,
  assignee_harmonized
FROM `patents-public-data.patents.publications`
WHERE publication_number IN UNNEST(numbers);
