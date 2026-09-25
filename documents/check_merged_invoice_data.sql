-- READ ONLY. Run in the configured invoice database, not necessarily Docmate.
-- Checks the five invoice numbers in CT002_2026_09_07_10_16_31_707.pdf.
SELECT DB_NAME() AS CurrentDatabase;

IF OBJECT_ID('dbo.DocMate_invoice_headers', 'U') IS NOT NULL
BEGIN
    SELECT id AS HeaderId, doc_number, doc_date, cms_customer_id,
           filename, invoice_fingerprint
    FROM dbo.DocMate_invoice_headers
    WHERE doc_number IN ('65715', '65664', '65543', '65549', '65387')
    ORDER BY doc_number, id;
END
ELSE
    SELECT 'Header table is not in this database. Select the worker invoice database.' AS Diagnostic;

IF OBJECT_ID('dbo.DocMate_invoice_line_Data', 'U') IS NOT NULL
BEGIN
    SELECT doc_number, doc_date, cms_customer_id, invoice_fingerprint,
           COUNT(*) AS SavedLineCount, MIN(id) AS FirstLineId, MAX(id) AS LastLineId
    FROM dbo.DocMate_invoice_line_Data
    WHERE doc_number IN ('65715', '65664', '65543', '65549', '65387')
    GROUP BY doc_number, doc_date, cms_customer_id, invoice_fingerprint
    ORDER BY doc_number;
END
ELSE
    SELECT 'Line table is not in this database. Select the worker invoice database.' AS Diagnostic;

-- Queue information is in the separate Docmate database.
IF OBJECT_ID('Docmate.dbo.UploadedFileInvoices', 'U') IS NOT NULL
BEGIN
    SELECT TOP (30) UploadedFileId, InvoiceOrdinal, InvoiceDatabase, DocType,
           HeaderId, InvoiceNumber, InvoiceDate, LineIdStart, LineIdEnd,
           InvoiceFingerprint
    FROM Docmate.dbo.UploadedFileInvoices
    WHERE InvoiceNumber IN ('65715', '65664', '65543', '65549', '65387')
    ORDER BY UploadedFileId DESC, InvoiceOrdinal;
END
ELSE
    SELECT 'Upload link table is missing or not accessible in Docmate.' AS Diagnostic;
