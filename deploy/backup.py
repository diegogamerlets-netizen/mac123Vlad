"""Резервная копия данных заказов: orders.json и все макеты/фото, привязанные к заказам.

Складывает архив в /opt/oblik/backups (или BACKUP_DIR) и хранит последние BACKUP_KEEP (14) штук.
ВАЖНО: эти копии лежат на том же сервере. Раз в неделю скачивайте свежий архив к себе
(например: scp -i ключ root@сервер:/opt/oblik/backups/<файл> .), либо включите автоматические
резервные копии диска в панели Selectel.
"""
import json
import os
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
data = ROOT / "data"
dest = Path(os.environ.get("BACKUP_DIR", "/opt/oblik/backups"))
keep = int(os.environ.get("BACKUP_KEEP", "14"))
dest.mkdir(parents=True, exist_ok=True)

orders_file = data / "orders.json"
if not orders_file.exists():
    print("заказов ещё нет, копировать нечего")
    raise SystemExit(0)

used = set()
for o in json.loads(orders_file.read_text(encoding="utf-8")).values():
    used.update(o.get("generation_ids") or [o.get("generation_id")])

name = dest / f"oblik-data-{time.strftime('%Y%m%d-%H%M')}.tar.gz"
with tarfile.open(name, "w:gz") as tar:
    tar.add(orders_file, arcname="data/orders.json")
    for g in used:
        folder = data / "gen" / str(g)
        if folder.is_dir():
            tar.add(folder, arcname=f"data/gen/{g}")
os.chmod(name, 0o600)

for old in sorted(dest.glob("oblik-data-*.tar.gz"))[:-keep]:
    old.unlink()
print("создан", name, f"{name.stat().st_size / 1e6:.1f} МБ")
