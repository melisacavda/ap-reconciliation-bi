-- =====================================================================
-- Raporlama view'ları
-- Bu proje taşınabilirlik için SQLite kullanır; kurumsal ortamda aynı view'lar
-- Oracle üzerinde bulunur. Oracle karşılıkları her view'ın altında not edilmiştir.
-- =====================================================================

-- 1) Fatura detayı: alt defter (satın alma faturaları) + tedarikçi + proje
DROP VIEW IF EXISTS v_ap_invoices;
CREATE VIEW v_ap_invoices AS
SELECT  i.invoice_id,
        i.invoice_date,
        strftime('%Y-%m', i.invoice_date)      AS period,      -- Oracle: TO_CHAR(i.invoice_date, 'YYYY-MM')
        i.vendor_id,
        v.vendor_name,
        i.project_id,
        p.project_name,
        p.segment,
        p.country,
        i.account_code,
        a.account_name,
        i.net_amount,
        i.vat_amount,
        i.gross_amount
FROM    ap_invoices i
JOIN    vendors v            ON v.vendor_id    = i.vendor_id
JOIN    projects p           ON p.project_id   = i.project_id
JOIN    chart_of_accounts a  ON a.account_code = i.account_code;

-- 2) Yevmiye satırları: genel muhasebe + hesap planı + proje
DROP VIEW IF EXISTS v_gl_lines;
CREATE VIEW v_gl_lines AS
SELECT  g.entry_no,
        g.line_no,
        g.posting_date,
        strftime('%Y-%m', g.posting_date)      AS period,
        g.account_code,
        a.account_name,
        a.account_type,
        g.project_id,
        p.segment,
        g.vendor_id,
        g.debit,
        g.credit,
        g.source_doc,
        g.description
FROM    gl_journal_lines g
JOIN    chart_of_accounts a  ON a.account_code = g.account_code
LEFT JOIN projects p         ON p.project_id   = g.project_id;

-- 3) Mizan (trial balance): hesap bazında borç / alacak toplamları
DROP VIEW IF EXISTS v_trial_balance;
CREATE VIEW v_trial_balance AS
SELECT  account_code,
        account_name,
        account_type,
        ROUND(SUM(debit), 2)                   AS total_debit,
        ROUND(SUM(credit), 2)                  AS total_credit,
        ROUND(SUM(debit) - SUM(credit), 2)     AS balance
FROM    v_gl_lines
GROUP BY account_code, account_name, account_type;

-- 4) Faturaların muhasebeleşmesi: 320 Satıcılar hesabına alacak kaydı, belge bazında
DROP VIEW IF EXISTS v_ap_postings;
CREATE VIEW v_ap_postings AS
SELECT  source_doc,
        COUNT(DISTINCT entry_no)               AS posting_count,
        ROUND(SUM(credit), 2)                  AS gl_gross_amount,
        MIN(project_id)                        AS gl_project_id,
        MIN(period)                            AS gl_period,
        MIN(vendor_id)                         AS gl_vendor_id
FROM    v_gl_lines
WHERE   account_code = '320'
  AND   credit > 0
  AND   source_doc LIKE 'INV-%'
GROUP BY source_doc;

-- 5) Muavin: 320 Satıcılar hesabının tedarikçi bazında hareketleri
DROP VIEW IF EXISTS v_vendor_ledger;
CREATE VIEW v_vendor_ledger AS
SELECT  g.vendor_id,
        v.vendor_name,
        g.posting_date,
        g.entry_no,
        g.source_doc,
        g.description,
        g.debit,
        g.credit
FROM    v_gl_lines g
JOIN    vendors v ON v.vendor_id = g.vendor_id
WHERE   g.account_code = '320';
