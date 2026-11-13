"""
KVKK — Kişisel ve hassas verilerin maskelenmesi.

İlke: Ham veri yalnızca veritabanında kalır. Rapor, dashboard ve Excel çıktılarına
çıkan her hassas alan maskelenir; analiz için gerekmeyen kişisel alanlar
(irtibat kişisi, telefon) çıktıya hiç taşınmaz (veri minimizasyonu).
"""
from __future__ import annotations

import pandas as pd

SENSITIVE_DROP = ["contact_name", "contact_phone"]  # analizde gereksiz -> hiç taşıma


def mask_tax_no(v: str | None) -> str | None:
    if not isinstance(v, str) or len(v) < 4:
        return v
    return "*" * (len(v) - 4) + v[-4:]


def mask_iban(v: str | None) -> str | None:
    if not isinstance(v, str) or len(v) < 8:
        return v
    return v[:4] + " **** **** **** **** " + v[-4:]


def mask_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.drop(columns=[c for c in SENSITIVE_DROP if c in df.columns]).copy()
    if "tax_no" in out:
        out["tax_no"] = out["tax_no"].map(mask_tax_no)
    if "iban" in out:
        out["iban"] = out["iban"].map(mask_iban)
    return out
