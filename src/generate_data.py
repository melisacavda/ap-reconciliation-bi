"""
Sentetik veri üreticisi — Synthetic data generator.

Bir inşaat/altyapı grubunun 2026 H1 satın alma faturalarını (alt defter / subledger)
ve bunların yevmiye kayıtlarını (genel muhasebe / GL) üretir. Gerçekçi olması için
mutabakat sırasında yakalanması gereken HATALAR bilinçli olarak eklenir ve
`data/known_errors.csv` dosyasına "cevap anahtarı" olarak yazılır. Testler,
mutabakat motorunun bu hataların hepsini (ve yalnızca bunları) bulduğunu doğrular.

Tüm isimler, vergi numaraları ve IBAN'lar rastgele üretilmiştir; gerçek kişi veya
kurumlarla ilgisi yoktur.
"""
from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

SEED = 42
ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "erp.db"

# --- Tekdüzen Hesap Planı (kullanılan hesaplar) -----------------------------
ACCOUNTS = [
    ("102", "Bankalar", "Aktif"),
    ("150", "İlk Madde ve Malzeme", "Aktif"),
    ("191", "İndirilecek KDV", "Aktif"),
    ("255", "Demirbaşlar", "Aktif"),
    ("320", "Satıcılar", "Pasif"),
    ("740", "Hizmet Üretim Maliyeti", "Gider"),
    ("770", "Genel Yönetim Giderleri", "Gider"),
]
EXPENSE_ACCOUNTS = ["150", "740", "770", "255"]
EXPENSE_WEIGHTS = [0.45, 0.35, 0.15, 0.05]

PROJECTS = [
    ("PRJ-01", "Kuzey Otoyolu Viyadük Paketi", "Altyapı", "Türkiye"),
    ("PRJ-02", "Merkez İçme Suyu Arıtma Tesisi", "Su & Atık Su", "Türkiye"),
    ("PRJ-03", "Liman Konut Kompleksi", "İnşaat", "Azerbaycan"),
    ("PRJ-04", "Şehir Hastanesi Ek Blok", "Sağlık", "Türkiye"),
    ("PRJ-05", "Sahil Termal Otel", "Turizm", "Gürcistan"),
    ("PRJ-06", "Atık Su Kolektör Hattı", "Su & Atık Su", "Kazakistan"),
    ("PRJ-07", "Alışveriş Merkezi Genişleme", "Perakende", "Türkiye"),
    ("PRJ-08", "Doğu Çevre Yolu", "Altyapı", "Irak"),
]

VENDOR_A = ["Anadolu", "Marmara", "Ege", "Kuzey", "Güney", "Yıldız", "Atlas", "Delta",
            "Kartal", "Toros", "Zirve", "Pamir", "Ekin", "Asya", "Mavi"]
VENDOR_B = ["Beton", "Demir Çelik", "İnşaat Malz.", "Hafriyat", "Makina Kiralama",
            "Elektrik", "Mekanik", "Lojistik", "Yapı Kimyasalları", "Akaryakıt",
            "Mühendislik", "İş Güvenliği"]
FIRST = ["Ahmet", "Ayşe", "Mehmet", "Elif", "Can", "Zeynep", "Murat", "Selin", "Emre", "Deniz"]
LAST = ["Yılmaz", "Kaya", "Demir", "Şahin", "Çelik", "Arslan", "Doğan", "Koç", "Aydın", "Öztürk"]

START, END = date(2026, 1, 1), date(2026, 6, 30)


def _rand_date(rng: random.Random) -> date:
    return START + timedelta(days=rng.randint(0, (END - START).days))


def build_master(rng: random.Random):
    accounts = pd.DataFrame(ACCOUNTS, columns=["account_code", "account_name", "account_type"])
    projects = pd.DataFrame(PROJECTS, columns=["project_id", "project_name", "segment", "country"])

    vendors, used = [], set()
    while len(vendors) < 24:
        name = f"{rng.choice(VENDOR_A)} {rng.choice(VENDOR_B)} A.Ş."
        if name in used:
            continue
        used.add(name)
        vendors.append({
            "vendor_id": f"V{len(vendors) + 1:03d}",
            "vendor_name": name,
            # Kişisel / hassas alanlar — çıktılarda maskelenir (KVKK)
            "tax_no": "".join(str(rng.randint(0, 9)) for _ in range(10)),
            "iban": "TR" + "".join(str(rng.randint(0, 9)) for _ in range(24)),
            "contact_name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
            "contact_phone": "+90 5" + "".join(str(rng.randint(0, 9)) for _ in range(9)),
        })
    return accounts, projects, pd.DataFrame(vendors)


def build_invoices(rng: random.Random, vendors: pd.DataFrame, n: int = 620) -> pd.DataFrame:
    rows = []
    for i in range(1, n + 1):
        acc = rng.choices(EXPENSE_ACCOUNTS, EXPENSE_WEIGHTS)[0]
        net = round(rng.lognormvariate(11.2, 0.9), 2)  # ~ 20k–700k TL arası tipik
        vat_rate = 0.10 if acc == "150" and rng.random() < 0.3 else 0.20
        vat = round(net * vat_rate, 2)
        rows.append({
            "invoice_id": f"INV-2026-{i:05d}",
            "vendor_id": rng.choice(vendors["vendor_id"].tolist()),
            "project_id": rng.choice([p[0] for p in PROJECTS]),
            "invoice_date": _rand_date(rng).isoformat(),
            "account_code": acc,
            "net_amount": net,
            "vat_rate": vat_rate,
            "vat_amount": vat,
            "gross_amount": round(net + vat, 2),
        })
    return pd.DataFrame(rows)


def _journal_for_invoice(inv: dict, entry_no: int, posting_date: str,
                         project_id: str, gross: float | None = None) -> list[dict]:
    """Fatura için dengeli yevmiye kaydı: Dr gider + Dr 191 / Cr 320."""
    gross = inv["gross_amount"] if gross is None else gross
    vat = inv["vat_amount"]
    net = round(gross - vat, 2)
    base = {"entry_no": f"JE-{entry_no:06d}", "posting_date": posting_date,
            "project_id": project_id, "source_doc": inv["invoice_id"]}
    return [
        {**base, "line_no": 1, "account_code": inv["account_code"], "vendor_id": None,
         "debit": net, "credit": 0.0, "description": "Alış faturası - gider"},
        {**base, "line_no": 2, "account_code": "191", "vendor_id": None,
         "debit": vat, "credit": 0.0, "description": "Alış faturası - KDV"},
        {**base, "line_no": 3, "account_code": "320", "vendor_id": inv["vendor_id"],
         "debit": 0.0, "credit": gross, "description": "Alış faturası - satıcı"},
    ]


def _transpose(amount: float) -> float:
    """Klasik veri girişi hatası: iki rakamın yer değiştirmesi (ör. 45.120 -> 54.120)."""
    s = f"{amount:.2f}"
    digits = s.replace(".", "")
    for i in range(len(digits) - 3):  # kuruş hanelerine dokunma
        if digits[i] != digits[i + 1] and not (i == 0 and digits[1] == "0"):
            d = list(digits)
            d[i], d[i + 1] = d[i + 1], d[i]
            new = "".join(d)
            return round(int(new) / 100, 2)
    return round(amount + 1000, 2)


def build_gl(rng: random.Random, invoices: pd.DataFrame):
    inv_records = invoices.to_dict("records")
    ids = [r["invoice_id"] for r in inv_records]
    pool = ids.copy()
    rng.shuffle(pool)

    # --- Bilinçli eklenen hatalar (cevap anahtarı) ---------------------------
    plan = {
        "MISSING_IN_GL": pool[0:4],          # faturası var, muhasebe kaydı yok
        "DUPLICATE_IN_GL": pool[4:7],        # iki kez kaydedilmiş
        "AMOUNT_MISMATCH": pool[7:11],       # tutar hatalı girilmiş (rakam kayması)
        "PROJECT_MISMATCH": pool[11:14],     # yanlış projeye kodlanmış
    }
    # Dönem (cut-off) hatası: ay sonuna yakın faturalar bir sonraki aya kaydedilmiş
    cutoff_candidates = [r["invoice_id"] for r in inv_records
                         if int(r["invoice_date"][8:10]) >= 27 and r["invoice_date"][5:7] != "06"
                         and r["invoice_id"] not in pool[:14]]
    plan["PERIOD_MISMATCH"] = cutoff_candidates[:3]

    status = {}
    for k, v in plan.items():
        for inv_id in v:
            status[inv_id] = k

    lines, known, entry_no = [], [], 1
    project_ids = [p[0] for p in PROJECTS]
    for inv in inv_records:
        st = status.get(inv["invoice_id"])
        if st == "MISSING_IN_GL":
            known.append({"doc_id": inv["invoice_id"], "error_type": st})
            continue
        posting_date, project, gross = inv["invoice_date"], inv["project_id"], None
        if st == "AMOUNT_MISMATCH":
            gross = _transpose(inv["gross_amount"])
        elif st == "PROJECT_MISMATCH":
            project = rng.choice([p for p in project_ids if p != inv["project_id"]])
        elif st == "PERIOD_MISMATCH":
            d = date.fromisoformat(inv["invoice_date"])
            posting_date = (date(d.year, d.month + 1, 1) + timedelta(days=rng.randint(0, 3))).isoformat()
        lines += _journal_for_invoice(inv, entry_no, posting_date, project, gross)
        entry_no += 1
        if st == "DUPLICATE_IN_GL":
            lines += _journal_for_invoice(inv, entry_no, posting_date, project, gross)
            entry_no += 1
        if st:
            known.append({"doc_id": inv["invoice_id"], "error_type": st})

    # Alt defterde karşılığı olmayan manuel kayıtlar (ör. silinmiş/iptal faturaya kayıt)
    vendors_used = list({r["vendor_id"] for r in inv_records})
    for k in range(2):
        ghost = {"invoice_id": f"INV-2026-9{k + 1:04d}", "vendor_id": rng.choice(vendors_used),
                 "account_code": "740", "vat_amount": 4000.0 * (k + 1), "gross_amount": 24000.0 * (k + 1)}
        lines += _journal_for_invoice(ghost, entry_no, _rand_date(rng).isoformat(), rng.choice(project_ids))
        entry_no += 1
        known.append({"doc_id": ghost["invoice_id"], "error_type": "MISSING_IN_SUBLEDGER"})

    # Ödemeler: faturaların ~%60'ı vadesinde ödenmiş (Dr 320 / Cr 102)
    for inv in inv_records:
        if inv["invoice_id"] in plan["MISSING_IN_GL"] or rng.random() > 0.6:
            continue
        pay_date = date.fromisoformat(inv["invoice_date"]) + timedelta(days=rng.choice([30, 45, 60]))
        if pay_date > END:
            continue
        base = {"entry_no": f"JE-{entry_no:06d}", "posting_date": pay_date.isoformat(),
                "project_id": inv["project_id"], "source_doc": f"PAY-{inv['invoice_id'][4:]}"}
        lines += [
            {**base, "line_no": 1, "account_code": "320", "vendor_id": inv["vendor_id"],
             "debit": inv["gross_amount"], "credit": 0.0, "description": "Satıcı ödemesi"},
            {**base, "line_no": 2, "account_code": "102", "vendor_id": None,
             "debit": 0.0, "credit": inv["gross_amount"], "description": "Satıcı ödemesi - banka"},
        ]
        entry_no += 1

    gl = pd.DataFrame(lines)[["entry_no", "line_no", "posting_date", "account_code", "project_id",
                              "vendor_id", "debit", "credit", "source_doc", "description"]]
    return gl, pd.DataFrame(known).sort_values(["error_type", "doc_id"]).reset_index(drop=True)


def main() -> None:
    rng = random.Random(SEED)
    DATA_DIR.mkdir(exist_ok=True)
    accounts, projects, vendors = build_master(rng)
    invoices = build_invoices(rng, vendors)
    gl, known = build_gl(rng, invoices)

    if DB_PATH.exists():
        DB_PATH.unlink()
    with sqlite3.connect(DB_PATH) as con:
        accounts.to_sql("chart_of_accounts", con, index=False)
        projects.to_sql("projects", con, index=False)
        vendors.to_sql("vendors", con, index=False)
        invoices.to_sql("ap_invoices", con, index=False)
        gl.to_sql("gl_journal_lines", con, index=False)
        con.executescript((ROOT / "sql" / "01_views.sql").read_text(encoding="utf-8"))
    known.to_csv(DATA_DIR / "known_errors.csv", index=False)
    print(f"[generate] {len(invoices)} fatura, {gl['entry_no'].nunique()} yevmiye fişi, "
          f"{len(gl)} satır, {len(known)} bilinçli hata -> {DB_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
