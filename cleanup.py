"""Очистка старых макетов и фото (запускается раз в сутки из cron).

Удаляет папки data/gen/<id>, которые НЕ привязаны ни к одному заказу и старше GEN_RETENTION_DAYS (по умолчанию 30):
это попытки создать макет, после которых заказ не оформили. Папки заказов не трогаются.
Срок хранения заказанных макетов и фото задайте в политике конфиденциальности и здесь вручную, когда решите.
Запуск вручную:  py cleanup.py [--dry-run]
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
env = ROOT / ".env"
if env.exists():
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

days = int(os.environ.get("GEN_RETENTION_DAYS", "30"))
dry = "--dry-run" in sys.argv
gen_dir = ROOT / "data" / "gen"
orders_file = ROOT / "data" / "orders.json"

used = set()
if orders_file.exists():
    for o in json.loads(orders_file.read_text(encoding="utf-8")).values():
        used.update(o.get("generation_ids") or [o.get("generation_id")])

removed = freed = 0
if gen_dir.exists():
    limit = time.time() - days * 86400
    for d in gen_dir.iterdir():
        if d.is_dir() and d.name not in used and d.stat().st_mtime < limit:
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            if not dry:
                shutil.rmtree(d, ignore_errors=True)
            removed += 1
            freed += size
print(f"{'[пробный запуск] ' if dry else ''}удалено папок: {removed}, освобождено: {freed / 1e6:.1f} МБ (срок {days} дн.)")
