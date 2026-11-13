"""
Mutabakat motoru — Reconciliation engine.

1. Mizan kontrolü: Toplam borç = toplam alacak (dönem bazında).
2. Alt defter ↔ genel muhasebe mutabakatı: Her fatura 320 Satıcılar hesabında
   doğru tutar, doğru proje ve doğru dönemle tam bir kez kaydedilmiş mi?
3. Farkların açıklanması: Toplam fark = istisnaların farkları toplamı mı?
   (Açıklanamayan fark 0 olmalı; değilse rapor teslim edilmez.)
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "erp.db"
TOLERANCE = 0.01  # kuruş yuvarlama toleransı

STATUS_ORDER = [
    "MISSING_IN_GL",
    "MISSING_IN_SUBLEDGER",
    "DUPLICATE_IN_GL",
    "AMOUNT_MISMATCH",
    "PROJECT_MISMATCH",
    "PERIOD_MISMATCH",
]
STATUS_TR = {
    "MATCHED": "Eşleşti",
    "MISSING_IN_GL": "Muhasebe kaydı yok",
    "MISSING_IN_SUBLEDGER": "Faturası yok (manuel kayıt)",
    "DUPLICATE_IN_GL": "Mükerrer kayıt",
    "AMOUNT_MISMATCH": "Tutar farkı",
    "PROJECT_MISMATCH": "Yanlış proje kodu",
    "PERIOD_MISMATCH": "Dönem (cut-off) farkı",
}
ACTION_TR = {
    "MISSING_IN_GL": "Faturayı muhasebeleştir",
    "MISSING_IN_SUBLEDGER": "Kaydın dayanağını sor; dayanak yoksa ters kayıt",
    "DUPLICATE_IN_GL": "Mükerrer fişi iptal et",
    "AMOUNT_MISMATCH": "Fişi fatura tutarıyla düzelt",
    "PROJECT_MISMATCH": "Proje kodunu düzelt (virman)",
    "PERIOD_MISMATCH": "Dönem kaydını düzelt / tahakkuk",
}


@dataclass
class ReconResult:
    detail: pd.DataFrame          # belge bazında tüm satırlar
    exceptions: pd.DataFrame      # yalnızca istisnalar
    trial_balance: pd.DataFrame   # mizan
    tb_by_period: pd.DataFrame    # dönem bazında borç/alacak dengesi
    summary: dict


def load(con: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    q = lambda sql: pd.read_sql(sql, con)  # noqa: E731
    return {
        "invoices": q("SELECT * FROM v_ap_invoices"),
        "postings": q("SELECT * FROM v_ap_postings"),
        "gl": q("SELECT * FROM v_gl_lines"),
        "tb": q("SELECT * FROM v_trial_balance ORDER BY account_code"),
    }


def classify(row: pd.Series) -> str:
    if pd.isna(row["gl_gross_amount"]):
        return "MISSING_IN_GL"
    if pd.isna(row["gross_amount"]):
        return "MISSING_IN_SUBLEDGER"
    if row["posting_count"] > 1:
        return "DUPLICATE_IN_GL"
    if abs(row["gross_amount"] - row["gl_gross_amount"]) > TOLERANCE:
        return "AMOUNT_MISMATCH"
    if row["project_id"] != row["gl_project_id"]:
        return "PROJECT_MISMATCH"
    if row["period"] != row["gl_period"]:
        return "PERIOD_MISMATCH"
    return "MATCHED"


def reconcile(con: sqlite3.Connection) -> ReconResult:
    d = load(con)
    inv, post = d["invoices"], d["postings"]

    detail = inv.merge(post, how="outer", left_on="invoice_id", right_on="source_doc", indicator=True)
    detail["doc_id"] = detail["invoice_id"].fillna(detail["source_doc"])
    detail["status"] = detail.apply(classify, axis=1)
    detail["subledger_amount"] = detail["gross_amount"].fillna(0.0)
    detail["gl_amount"] = detail["gl_gross_amount"].fillna(0.0)
    detail["difference"] = (detail["gl_amount"] - detail["subledger_amount"]).round(2)
    detail["status_tr"] = detail["status"].map(STATUS_TR)
    detail["action_tr"] = detail["status"].map(ACTION_TR)
    detail["invoice_project"] = detail["project_id"]
    detail["invoice_period"] = detail["period"]
    detail["project_id"] = detail["project_id"].fillna(detail["gl_project_id"])
    detail["vendor_id"] = detail["vendor_id"].fillna(detail["gl_vendor_id"])
    detail["period"] = detail["period"].fillna(detail["gl_period"])

    exceptions = (detail[detail["status"] != "MATCHED"]
                  .assign(_o=lambda x: x["status"].map(STATUS_ORDER.index))
                  .sort_values(["_o", "doc_id"]).drop(columns="_o"))

    # --- Mizan kontrolü ----------------------------------------------------
    gl = d["gl"]
    tb_by_period = (gl.groupby("period")[["debit", "credit"]].sum().round(2)
                    .assign(difference=lambda x: (x["debit"] - x["credit"]).round(2))
                    .reset_index())

    # --- Toplamlar ve "açıklanamayan fark" ---------------------------------
    sub_total = round(inv["gross_amount"].sum(), 2)
    gl_total = round(gl.loc[(gl["account_code"] == "320") & gl["source_doc"].str.startswith("INV-"),
                            "credit"].sum(), 2)
    total_diff = round(gl_total - sub_total, 2)
    explained = round(exceptions["difference"].sum(), 2)

    summary = {
        "invoice_count": int(len(inv)),
        "gl_entry_count": int(gl["entry_no"].nunique()),
        "subledger_total": sub_total,
        "gl_total": gl_total,
        "total_difference": total_diff,
        "explained_difference": explained,
        "unexplained_difference": round(total_diff - explained, 2),
        "matched_count": int((detail["status"] == "MATCHED").sum()),
        "exception_count": int(len(exceptions)),
        "match_rate": round((detail["status"] == "MATCHED").mean(), 4),
        "trial_balance_ok": bool((tb_by_period["difference"].abs() <= TOLERANCE).all()),
        "exceptions_by_type": exceptions["status"].value_counts().reindex(STATUS_ORDER, fill_value=0).to_dict(),
    }
    return ReconResult(detail, exceptions, d["tb"], tb_by_period, summary)


def run(db_path: Path = DB_PATH) -> ReconResult:
    with sqlite3.connect(db_path) as con:
        return reconcile(con)


if __name__ == "__main__":
    r = run()
    for k, v in r.summary.items():
        print(f"{k:24s} {v}")
