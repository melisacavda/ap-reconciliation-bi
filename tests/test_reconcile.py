"""
Doğrulama testleri: Mutabakat motoru kaynak veriyle birebir tutuyor mu?

Çalıştırma:  python -m pytest -q
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import generate_data  # noqa: E402
import reconcile  # noqa: E402
from privacy import mask_iban, mask_tax_no, mask_frame  # noqa: E402


@pytest.fixture(scope="module")
def result():
    generate_data.main()
    return reconcile.run()


@pytest.fixture(scope="module")
def known():
    return pd.read_csv(ROOT / "data" / "known_errors.csv")


def test_trial_balance_is_balanced(result):
    """Mizan: her dönemde toplam borç = toplam alacak."""
    assert result.summary["trial_balance_ok"]
    assert result.trial_balance["balance"].sum() == pytest.approx(0, abs=0.01)


def test_all_known_errors_detected(result, known):
    """Bilinçli eklenen her hata, doğru türüyle bulunmalı."""
    found = result.exceptions.set_index("doc_id")["status"]
    for _, row in known.iterrows():
        assert found.get(row.doc_id) == row.error_type, row.doc_id


def test_no_false_positives(result, known):
    """Temiz faturalar istisna olarak işaretlenmemeli."""
    assert set(result.exceptions["doc_id"]) == set(known["doc_id"])


def test_difference_fully_explained(result):
    """Toplam fark = istisna farkları toplamı (açıklanamayan fark 0)."""
    assert result.summary["unexplained_difference"] == pytest.approx(0, abs=0.01)


def test_totals_tie_to_source_with_independent_sql(result):
    """Python toplamları, doğrudan tablolara yazılan bağımsız SQL ile birebir tutmalı."""
    with sqlite3.connect(reconcile.DB_PATH) as con:
        sub = con.execute("SELECT ROUND(SUM(gross_amount), 2) FROM ap_invoices").fetchone()[0]
        gl = con.execute("SELECT ROUND(SUM(credit), 2) FROM gl_journal_lines "
                         "WHERE account_code = '320' AND source_doc LIKE 'INV-%'").fetchone()[0]
    assert result.summary["subledger_total"] == pytest.approx(sub, abs=0.01)
    assert result.summary["gl_total"] == pytest.approx(gl, abs=0.01)


def test_every_invoice_classified_once(result):
    """Her belge tam bir kez sınıflandırılmalı (kayıp ya da çift satır yok)."""
    assert result.detail["doc_id"].is_unique
    assert result.summary["matched_count"] + result.summary["exception_count"] == len(result.detail)


def test_kvkk_masking():
    assert mask_tax_no("1234567890") == "******7890"
    assert mask_iban("TR120006200000000123456789").endswith("6789")
    assert "0006200000" not in mask_iban("TR120006200000000123456789")
    df = pd.DataFrame({"tax_no": ["1234567890"], "contact_name": ["Ad Soyad"], "contact_phone": ["+90"]})
    out = mask_frame(df)
    assert "contact_name" not in out and "contact_phone" not in out
