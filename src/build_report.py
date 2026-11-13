"""
Rapor üretimi — Excel raporu + HTML dashboard.

Tüm çıktılar maskelenmiş veriden üretilir (bkz. privacy.py).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from privacy import mask_frame
from reconcile import ACTION_TR, DB_PATH, STATUS_ORDER, STATUS_TR, ReconResult

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
TEMPLATE = Path(__file__).parent / "templates" / "dashboard.html"


def _vendors(con) -> pd.DataFrame:
    return mask_frame(pd.read_sql("SELECT * FROM vendors", con))


def _projects(con) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM projects ORDER BY project_id", con)


def exception_table(r: ReconResult, vendors: pd.DataFrame) -> pd.DataFrame:
    x = r.exceptions.merge(vendors[["vendor_id", "vendor_name", "tax_no"]], on="vendor_id",
                           how="left", suffixes=("_drop", ""))
    x = x.drop(columns=[c for c in x.columns if c.endswith("_drop")])
    return pd.DataFrame({
        "doc_id": x["doc_id"],
        "status": x["status"],
        "status_tr": x["status_tr"],
        "project_id": x["project_id"],
        "invoice_project": x["invoice_project"],
        "gl_project": x["gl_project_id"],
        "invoice_period": x["invoice_period"],
        "gl_period": x["gl_period"],
        "vendor_name": x["vendor_name"],
        "tax_no": x["tax_no"],
        "subledger_amount": x["subledger_amount"].round(2),
        "gl_amount": x["gl_amount"].round(2),
        "difference": x["difference"].round(2),
        "action_tr": x["action_tr"],
    })


def dashboard_data(r: ReconResult, con) -> dict:
    vendors, projects = _vendors(con), _projects(con)
    inv = pd.read_sql("SELECT * FROM v_ap_invoices", con)
    gl = pd.read_sql("SELECT period, credit FROM v_gl_lines "
                     "WHERE account_code='320' AND credit>0 AND source_doc LIKE 'INV-%'", con)

    monthly = (inv.groupby("period")["gross_amount"].sum().rename("subledger").to_frame()
               .join(gl.groupby("period")["credit"].sum().rename("gl"), how="outer").fillna(0).round(2)
               .reset_index())

    exc = exception_table(r, vendors)
    by_type = (exc.groupby("status").agg(count=("doc_id", "size"), difference=("difference", "sum"))
               .reindex(STATUS_ORDER).fillna(0).reset_index())
    by_type["label"] = by_type["status"].map(STATUS_TR)
    by_type["action"] = by_type["status"].map(ACTION_TR)
    by_type["count"] = by_type["count"].astype(int)
    by_type = by_type.sort_values("count", ascending=False, kind="stable")

    proj = (inv.groupby("project_id").agg(net_cost=("net_amount", "sum"), invoice_count=("invoice_id", "size"))
            .join(exc.groupby("project_id").size().rename("exception_count")).fillna(0)
            .reset_index().merge(projects, on="project_id"))
    proj["exception_count"] = proj["exception_count"].astype(int)
    proj = proj.sort_values("net_cost", ascending=False)

    clean = lambda df: json.loads(df.to_json(orient="records", force_ascii=False))  # NaN -> null
    return {
        "generated_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
        "summary": r.summary,
        "monthly": clean(monthly),
        "by_type": clean(by_type),
        "projects": clean(proj),
        "exceptions": clean(exc),
        "trial_balance": clean(r.trial_balance),
    }


def write_dashboard(data: dict, path: Path) -> None:
    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    path.write_text(html, encoding="utf-8")


# --- Excel -------------------------------------------------------------------
HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
HEADER_FONT = Font(color="FFFFFF", bold=True)
MONEY = '#,##0.00'


def _style(ws, money_cols=(), widths=None):
    for c in ws[1]:
        c.fill, c.font = HEADER_FILL, HEADER_FONT
        c.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"
    for col in money_cols:
        for cell in ws[col][1:]:
            cell.number_format = MONEY
    for i, col in enumerate(ws.columns, 1):
        width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
        ws.column_dimensions[get_column_letter(i)].width = min(max(10, width + 2), 48)
    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions


def write_excel(r: ReconResult, con, path: Path) -> None:
    vendors, projects = _vendors(con), _projects(con)
    exc = exception_table(r, vendors)
    ledger = pd.read_sql("SELECT vendor_id, vendor_name, ROUND(SUM(debit),2) AS borc, "
                         "ROUND(SUM(credit),2) AS alacak, ROUND(SUM(credit)-SUM(debit),2) AS bakiye "
                         "FROM v_vendor_ledger GROUP BY vendor_id, vendor_name ORDER BY bakiye DESC", con)

    exc_out = exc[["doc_id", "status_tr", "project_id", "invoice_period", "gl_period", "vendor_name",
                   "tax_no", "subledger_amount", "gl_amount", "difference", "action_tr"]].rename(columns={
        "doc_id": "Belge No", "status_tr": "İstisna", "project_id": "Proje", "invoice_period": "Fatura Dönemi",
        "gl_period": "Kayıt Dönemi", "vendor_name": "Tedarikçi", "tax_no": "VKN (maskeli)",
        "subledger_amount": "Fatura Tutarı", "gl_amount": "Muhasebe Tutarı", "difference": "Fark",
        "action_tr": "Önerilen Aksiyon"})

    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame({"Kalem": [], "Değer": []}).to_excel(xw, sheet_name="Özet", index=False)
        exc_out.to_excel(xw, sheet_name="İstisnalar", index=False)
        r.trial_balance.rename(columns={"account_code": "Hesap", "account_name": "Hesap Adı",
                                        "account_type": "Tür", "total_debit": "Borç",
                                        "total_credit": "Alacak", "balance": "Bakiye"}
                               ).to_excel(xw, sheet_name="Mizan", index=False)
        r.tb_by_period.rename(columns={"period": "Dönem", "debit": "Borç", "credit": "Alacak",
                                       "difference": "Fark"}).to_excel(xw, sheet_name="Dönem Dengesi", index=False)
        ledger.rename(columns={"vendor_id": "Tedarikçi Kodu", "vendor_name": "Tedarikçi", "borc": "Borç",
                               "alacak": "Alacak", "bakiye": "Bakiye"}).to_excel(xw, sheet_name="Muavin 320", index=False)
        projects.rename(columns={"project_id": "Proje", "project_name": "Proje Adı", "segment": "Segment",
                                 "country": "Ülke"}).to_excel(xw, sheet_name="Projeler", index=False)

        wb = xw.book
        ws = wb["İstisnalar"]
        # Proje adını XLOOKUP ile Projeler sayfasından çek (Excel 365 / 2021+)
        ws.cell(1, 12, "Proje Adı (XLOOKUP)")
        for i in range(2, ws.max_row + 1):
            ws.cell(i, 12, f'=_xlfn.XLOOKUP(C{i},Projeler!$A:$A,Projeler!$B:$B,"—")')
        _style(ws, money_cols=("H", "I", "J"))
        _style(wb["Mizan"], money_cols=("D", "E", "F"))
        _style(wb["Dönem Dengesi"], money_cols=("B", "C", "D"))
        _style(wb["Muavin 320"], money_cols=("C", "D", "E"))
        _style(wb["Projeler"])

        # Özet sayfası: değerler + Excel formülleriyle bağımsız çapraz kontrol
        s, S = wb["Özet"], r.summary
        n = len(exc_out) + 1
        rows = [
            ("Fatura toplamı (alt defter)", S["subledger_total"]),
            ("Muhasebe toplamı (320 alacak)", S["gl_total"]),
            ("Toplam fark", "=B3-B2"),
            ("İstisna farkları toplamı", f"=SUM('İstisnalar'!J2:J{n})"),
            ("Açıklanamayan fark (0 olmalı)", "=ROUND(B4-B5,2)"),
            ("Mizan: toplam borç", "=SUM(Mizan!D:D)"),
            ("Mizan: toplam alacak", "=SUM(Mizan!E:E)"),
            ("Mizan dengede mi?", '=IF(ROUND(B7-B8,2)=0,"EVET","HAYIR")'),
            ("Fatura sayısı", S["invoice_count"]),
            ("İstisna sayısı", f"=COUNTA('İstisnalar'!A2:A{n})"),
            ("Eşleşme oranı", f"=1-B11/(B10+COUNTIF('İstisnalar'!B2:B{n},\"{STATUS_TR['MISSING_IN_SUBLEDGER']}\"))"),
        ]
        for i, (k, v) in enumerate(rows, start=2):
            s.cell(i, 1, k)
            s.cell(i, 2, v).number_format = MONEY
        s["B10"].number_format = s["B11"].number_format = "0"
        s["B12"].number_format = "0.00%"
        s["B9"].number_format = "General"
        _style(s)
        s.column_dimensions["A"].width, s.column_dimensions["B"].width = 34, 20
        s.cell(14, 1, "Not: Tüm tutarlar TL. VKN'ler KVKK gereği maskelenmiştir. Veriler sentetiktir.").font = Font(italic=True, color="666666")


def build(r: ReconResult, db_path: Path = DB_PATH) -> dict[str, Path]:
    DOCS.mkdir(exist_ok=True)
    out = {"dashboard": DOCS / "index.html", "excel": DOCS / "mutabakat_raporu.xlsx"}
    with sqlite3.connect(db_path) as con:
        write_dashboard(dashboard_data(r, con), out["dashboard"])
        write_excel(r, con, out["excel"])
    return out
