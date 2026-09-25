# Merged invoice upload links

The worker uses `docmate_app_v1.19.001_r01_customer.py` to split merged PDFs.
Each invoice keeps its own saved header and line fingerprint. The worker now
creates `Docmate.dbo.UploadedFileInvoices` with one ordered reference per invoice,
including invoices skipped as exact duplicates. Reprocessing replaces the upload's
links in the same transaction as its final status update.

For multi-invoice files, the legacy `UploadedFiles.HeaderId`, `LineIdStart` and
`LineIdEnd` are NULL. They cannot accurately represent several invoices.
`InvoiceNumber` remains a length-limited display summary; never use it as a key.
Single-invoice uploads retain the legacy fields.

## Deploy and reload

Deploy the updated worker, `uploaded_invoice_tracking.py`, and the 1.19.001 app
together and restart the worker/app. The worker creates the link table using its
existing schema setup connection. No live database migration was run during development.

In the Python app, Reports > Invoices in an uploaded file > Upload ID > Load all
invoices loads each linked invoice for the selected/logged-in customer separately.

External consumers must first read `UploadedFileInvoices` by `UploadedFileId` and
the configured invoice database, ordered by `InvoiceOrdinal`. For purchase invoices,
join its `HeaderId` AND `InvoiceFingerprint` to the invoice database's
`DocMate_invoice_headers`, and fetch `DocMate_invoice_line_Data` by that fingerprint.
Enforce the authenticated customer's `cms_customer_id` on both queries.
Do not concatenate database names from untrusted records into SQL; use the configured
invoice database/connection. `uploaded_invoice_tracking.load_invoices` implements
this contract. Do not use a global line-ID range or filter by the latest filename.

Old uploads must be reprocessed through the normal queue workflow to populate
links. Existing records are not deleted or repaired automatically. The previous
aggregate range cannot safely reconstruct membership.

Duplicate detection still uses the existing content fingerprint: identical
extractions reuse records; changed extracted values remain separate versions.
This change does not merge existing duplicates or alter that policy.

## Validation

`virtualenv310/Scripts/python.exe -m unittest discover -s tests -p test_uploaded_invoice_tracking.py -v`

The supplied 10-page PDF splits into 65715 (page 1), 65664 (pages 3–4),
65543 (pages 5–6), 65549 (page 7), and 65387 (page 9).
Tests cover real PDF splitting, duplicate reuse with links, exact multi-invoice
reload, customer isolation, and status/link commit behavior. SQL access is mocked;
live SQL Server integration and the separate external reload screen are unverified.
