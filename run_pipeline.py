"""
Tek komutla aylık mutabakat raporu:  python run_pipeline.py [--regenerate]

Akış:  (veri üret) -> SQL view'larından çek -> mutabakat -> KONTROL KAPISI -> Excel + dashboard

Kontrol kapısı: Açıklanamayan fark 0 değilse veya mizan dengede değilse rapor
ÜRETİLMEZ ve işlem hata koduyla biter. Yanlış rakam içeren bir raporun yönetime
gitmesindense hiç gitmemesi tercih edilir.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import build_report  # noqa: E402
import generate_data  # noqa: E402
import reconcile  # noqa: E402

(ROOT / "output").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s",
                    handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / "output" / "run_log.txt", encoding="utf-8")])
log = logging.getLogger("pipeline")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regenerate", action="store_true", help="Sentetik veritabanını yeniden üret")
    args = ap.parse_args()

    if args.regenerate or not reconcile.DB_PATH.exists():
        generate_data.main()

    r = reconcile.run()
    s = r.summary
    log.info("Fatura: %s | Fiş: %s | Eşleşen: %s | İstisna: %s",
             s["invoice_count"], s["gl_entry_count"], s["matched_count"], s["exception_count"])
    log.info(f"Toplam fark: {s['total_difference']:,.2f} TL | Açıklanan: {s['explained_difference']:,.2f} TL"
             f" | Açıklanamayan: {s['unexplained_difference']:,.2f} TL")

    gate = []
    if abs(s["unexplained_difference"]) > reconcile.TOLERANCE:
        gate.append("Açıklanamayan fark sıfır değil")
    if not s["trial_balance_ok"]:
        gate.append("Mizan dengede değil")
    if gate:
        for g in gate:
            log.error("KONTROL KAPISI: %s — rapor üretilmedi.", g)
        return 1

    out = build_report.build(r)
    for k, p in out.items():
        log.info("%s -> %s", k, p.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
