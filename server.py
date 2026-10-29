"""Облик — сервер сайта: генерация макетов, заказы, оплата через ЮKassa.

Только стандартная библиотека Python 3.9+. Запуск: python server.py
"""
import base64
import html
import json
import mimetypes
import os
import re
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import address as addr
import ai
import delivery
import yookassa

ROOT = Path(__file__).resolve().parent
mimetypes.add_type("image/webp", ".webp")
DOCS_VERSION = "2026-10-28"   # редакция оферты и политики; меняйте при изменении текстов


def load_env():
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()

PORT = int(os.environ.get("PORT", "8000"))
HOST = os.environ.get("HOST", "0.0.0.0")   # на сервере в .env: HOST=127.0.0.1 (наружу смотрит только nginx)
BASE_URL = os.environ.get("BASE_URL", f"http://localhost:{PORT}").rstrip("/")
PRICE_1 = int(os.environ.get("PRICE_ONE_PERSON", "5990"))      # 1 человек
PRICE_2 = int(os.environ.get("PRICE_TWO_PEOPLE", "9990"))      # 2 человека
PRICE_STEP = int(os.environ.get("PRICE_STEP", "5000"))         # +каждый следующий человек / элемент
MAX_PEOPLE = int(os.environ.get("MAX_PEOPLE", "6"))
MAX_ELEMENTS = int(os.environ.get("MAX_ELEMENTS", "0"))   # дополнительные элементы пока отключены


PRICE_GROWTH = int(os.environ.get("PRICE_GROWTH", "19990"))    # «Этапы взросления»: 3 фигурки
GROWTH_COUNT = 3


def composition(people, elements, product="sculpture"):
    if product == "growth":
        return f"Этапы взросления: {GROWTH_COUNT} фигурки"
    text = f"{people} чел."
    return text + (f" + {elements} доп. элемент(а)" if elements else "")


def calc_price(people, elements):
    base = PRICE_1 if people == 1 else PRICE_2 + (people - 2) * PRICE_STEP
    return base + elements * PRICE_STEP


def order_groups(order):
    """Макеты заказа: для обычной скульптуры одна группа, для «Этапов взросления» — три."""
    ids = order.get("generation_ids") or [order["generation_id"]]
    labels = order.get("labels") or [""] * len(ids)
    return [{"label": lb, "views": [f"/gen/{g}/{v}.png" for v in VIEWS], "generation_id": g}
            for g, lb in zip(ids, labels)]


ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
GEN_LIMIT_PER_HOUR = int(os.environ.get("GEN_LIMIT_PER_HOUR", "8"))
MAX_BODY = 12 * 1024 * 1024

DATA = ROOT / "data"
GEN_DIR = DATA / "gen"
GEN_DIR.mkdir(parents=True, exist_ok=True)
ORDERS_FILE = DATA / "orders.json"
lock = threading.Lock()

STYLES = {
    "wood": "Резьба по дереву",
    "marble": "Античный мрамор",
    "folk": "Народный орнамент",
    "bronze": "Бронза",
    "lowpoly": "Лоу-поли",
    "artdeco": "Арт-деко",
    "relief": "Барельеф-медальон",
}
VIEWS = ["front", "right", "back", "left"]
SIZES = {"10-15": "10–15 см"}

ID_RE = re.compile(r"^[0-9a-f]{32}$")
rate = {}
addr_rate = {}


# ---------- хранилище заказов ----------
def read_orders():
    if not ORDERS_FILE.exists():
        return {}
    return json.loads(ORDERS_FILE.read_text(encoding="utf-8"))


def write_orders(orders):
    tmp = ORDERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(orders, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ORDERS_FILE)


def get_order(order_id):
    with lock:
        return read_orders().get(order_id)


def update_order(order_id, **fields):
    with lock:
        orders = read_orders()
        if order_id not in orders:
            return None
        orders[order_id].update(fields)
        write_orders(orders)
        return orders[order_id]


def mark_paid(order_id):
    order = get_order(order_id)
    if order and order["status"] == "pending_payment":
        update_order(order_id, status="paid", paid_at=int(time.time()))


def sync_payment(order):
    """Проверяет статус платежа в ЮKassa (работает и без вебхука, и на localhost)."""
    if order["status"] != "pending_payment" or not order.get("payment_id") or order.get("demo_payment"):
        return order
    try:
        payment = yookassa.get_payment(order["payment_id"])
    except Exception as exc:  # сеть/ключи — не роняем страницу
        print("yookassa check failed:", exc)
        return order
    if payment.get("status") == "succeeded" and payment.get("paid"):
        mark_paid(order["id"])
    elif payment.get("status") == "canceled":
        update_order(order["id"], status="canceled")
    return get_order(order["id"])


def public_order(order):
    return {
        "id": order["id"],
        "status": order["status"],
        "style": STYLES.get(order["style"], order["style"]),
        "size": SIZES.get(order["size"], order["size"]),
        "composition": composition(order.get("people", 1), order.get("elements", 0), order.get("product", "sculpture")),
        "total": order["total"],
        "price": order["price"],
        "shipping": order["shipping"],
        "groups": [{"label": g["label"], "views": g["views"]} for g in order_groups(order)],
    }


# ---------- фоновые задачи генерации ----------
jobs = {}


def set_job(job_id, **fields):
    with lock:
        if job_id in jobs:
            jobs[job_id].update(fields)


def run_job(job_id, gen_id, folder, photo, style, people, elements, note, extras=None):
    def progress(step, pct):
        set_job(job_id, step=step, pct=pct)

    try:
        views = ai.generate_views(photo, style, people, elements, note, progress=progress, extras=extras)
        for name, data in views.items():
            (folder / f"{name}.png").write_bytes(data)
        set_job(job_id, status="done", step="Готово", pct=100, demo=ai.demo_mode(),
                views={v: f"/gen/{gen_id}/{v}.png" for v in VIEWS})
    except ai.AIError as exc:
        set_job(job_id, status="error", error=str(exc))
    except Exception as exc:
        print("job failed:", repr(exc))
        set_job(job_id, status="error", error="Не удалось создать макет. Попробуйте ещё раз.")
    # старые задачи убираем, чтобы память не росла
    with lock:
        for k in [k for k, v in jobs.items() if time.time() - v["created"] > 6 * 3600]:
            jobs.pop(k, None)


# ---------- HTTP ----------
class Handler(BaseHTTPRequestHandler):
    server_version = "Oblik/1.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    # --- helpers
    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body, ctype, status=200, cache="no-cache"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, cache="no-cache"):
        if not path.is_file():
            return self.send_json({"error": "Не найдено"}, 404)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self.send_bytes(path.read_bytes(), ctype, cache=cache)

    def send_legal(self, name):
        """Юридические страницы. Плашка «Черновик» исчезает сама, когда в тексте не осталось незаполненных полей."""
        text = (ROOT / "templates" / name).read_text(encoding="utf-8")
        if 'class="todo"' not in text:
            text = re.sub(r"<!--DRAFT-->.*?<!--/DRAFT-->", "", text, flags=re.S)
        self.send_bytes(text.encode("utf-8"), "text/html; charset=utf-8")

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            raise ValueError("Некорректный размер запроса")
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def client_ip(self):
        return self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()

    # --- routing
    def do_GET(self):
        path = urlparse(self.path).path
        pages = {
            "/": "index.html",
            "/create": "create.html",
            "/legal": "legal.html",
            "/offer": "offer.html",
            "/privacy": "privacy.html",
        }
        if path in ("/legal", "/offer", "/privacy"):
            return self.send_legal(pages[path])
        if path in pages:
            return self.send_file(ROOT / "templates" / pages[path])
        if path.startswith("/static/"):
            target = (ROOT / path.lstrip("/")).resolve()
            if ROOT / "static" in target.parents:
                # no-cache: браузер каждый раз проверяет файл, иначе правки картинок не доходят до пользователя.
                # На боевом сервере кеширование лучше настроить в nginx с версиями файлов.
                return self.send_file(target, cache="no-cache")
            return self.send_json({"error": "Не найдено"}, 404)
        m = re.fullmatch(r"/gen/([0-9a-f]{32})/(original|front|right|back|left)\.(png|jpg)", path)
        if m:
            if m.group(2) == "original":  # исходное фото не отдаём публично
                return self.send_json({"error": "Не найдено"}, 404)
            return self.send_file(GEN_DIR / m.group(1) / f"{m.group(2)}.png", cache="public, max-age=86400")
        if re.fullmatch(r"/order/[0-9a-f]{32}", path):
            return self.send_file(ROOT / "templates" / "order.html")
        m = re.fullmatch(r"/api/generate/([0-9a-f]{32})", path)
        if m:
            return self.api_job_status(m.group(1))
        m = re.fullmatch(r"/api/orders/([0-9a-f]{32})", path)
        if m:
            order = get_order(m.group(1))
            if not order:
                return self.send_json({"error": "Заказ не найден"}, 404)
            return self.send_json(public_order(sync_payment(order)))
        m = re.fullmatch(r"/pay-demo/([0-9a-f]{32})", path)
        if m and yookassa.demo_mode():
            return self.send_file(ROOT / "templates" / "pay_demo.html")
        if path == "/api/config":
            return self.send_json({
                "price1": PRICE_1, "price2": PRICE_2, "step": PRICE_STEP,
                "priceGrowth": PRICE_GROWTH, "growthCount": GROWTH_COUNT,
                "maxPeople": MAX_PEOPLE, "maxElements": MAX_ELEMENTS,
                "styles": STYLES, "sizes": SIZES,
                "aiDemo": ai.demo_mode(), "payDemo": yookassa.demo_mode(),
                "addressDemo": addr.demo_mode(), "deliveryDemo": delivery.demo_mode(),
            })
        if path == "/api/address/suggest":
            return self.api_address_suggest()
        if path == "/admin":
            return self.admin_page()
        mm = re.fullmatch(r"/admin/original/([0-9a-f]{32})(?:/(left|right|back))?", path)
        if mm:
            if not self.check_admin():
                return
            return self.send_file(GEN_DIR / mm.group(1) / (f"extra_{mm.group(2)}.jpg" if mm.group(2) else "original.jpg"))
        self.send_json({"error": "Не найдено"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/generate":
                return self.api_generate()
            if path == "/api/orders":
                return self.api_create_order()
            if path == "/api/delivery/quote":
                return self.api_delivery_quote()
            if path == "/api/yookassa/webhook":
                return self.api_webhook()
            m = re.fullmatch(r"/api/pay-demo/([0-9a-f]{32})", path)
            if m and yookassa.demo_mode():
                order = get_order(m.group(1))
                if not order:
                    return self.send_json({"error": "Заказ не найден"}, 404)
                mark_paid(order["id"])
                return self.send_json({"ok": True})
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            print("ERROR", path, repr(exc))
            return self.send_json({"error": "Внутренняя ошибка сервера"}, 500)
        self.send_json({"error": "Не найдено"}, 404)

    # --- API
    def api_generate(self):
        ip = self.client_ip()
        now = time.time()
        hits = [t for t in rate.get(ip, []) if now - t < 3600]
        if len(hits) >= GEN_LIMIT_PER_HOUR:
            return self.send_json({"error": "Слишком много попыток. Попробуйте через час."}, 429)
        body = self.read_json()
        style = body.get("style")
        if style not in STYLES:
            raise ValueError("Выберите стиль статуэтки")
        photo = parse_photo(body.get("photo", ""))
        extras = {}
        raw_extra = body.get("extra") if isinstance(body.get("extra"), dict) else {}
        for side in ("left", "right", "back"):
            if raw_extra.get(side):
                extras[side] = parse_photo(raw_extra[side])
        product = "growth" if body.get("product") == "growth" else "sculpture"
        if product == "growth":
            people, elements = 1, 0
        else:
            try:
                people = int(body.get("people", 1))
                elements = int(body.get("elements", 0))
            except (TypeError, ValueError):
                raise ValueError("Некорректный состав статуэтки")
            if not (1 <= people <= MAX_PEOPLE and 0 <= elements <= MAX_ELEMENTS):
                raise ValueError("Некорректный состав статуэтки")
        note = re.sub(r"\s+", " ", str(body.get("note", ""))).strip()[:150]
        label = re.sub(r"\s+", " ", str(body.get("label", ""))).strip()[:40]
        hits.append(now)
        rate[ip] = hits

        gen_id = uuid.uuid4().hex
        folder = GEN_DIR / gen_id
        folder.mkdir(parents=True)
        (folder / "original.jpg").write_bytes(photo)
        for side, data in extras.items():
            (folder / f"extra_{side}.jpg").write_bytes(data)
        # Состав фиксируем на сервере: цена заказа берётся отсюда, а не от клиента.
        (folder / "meta.json").write_text(json.dumps(
            {"style": style, "people": people, "elements": elements, "note": note,
             "product": product, "label": label}, ensure_ascii=False),
            encoding="utf-8")
        # Генерация занимает минуты — выполняем в фоне, браузер опрашивает статус (иначе прокси оборвёт запрос).
        job_id = uuid.uuid4().hex
        with lock:
            jobs[job_id] = {"status": "running", "step": "Начинаем", "pct": 3, "id": gen_id, "created": time.time()}
        threading.Thread(target=run_job, args=(job_id, gen_id, folder, photo, style, people, elements, note, extras), daemon=True).start()
        self.send_json({"job": job_id, "id": gen_id})

    def api_job_status(self, job_id):
        with lock:
            job = jobs.get(job_id)
            if job is None:
                return self.send_json({"error": "Задача не найдена — возможно, сервер перезапускался. Создайте макет заново."}, 404)
            data = dict(job)
        self.send_json(data)

    def api_address_suggest(self):
        if addr.demo_mode():
            return self.send_json({"demo": True, "items": []})
        ip = self.client_ip()
        now = time.time()
        hits = [t for t in addr_rate.get(ip, []) if now - t < 60]
        if len(hits) >= 60:
            return self.send_json({"error": "Слишком много запросов"}, 429)
        addr_rate[ip] = hits + [now]
        text = (parse_qs(urlparse(self.path).query).get("q") or [""])[0].strip()[:200]
        if len(text) < 3:
            return self.send_json({"items": []})
        try:
            return self.send_json({"items": addr.suggest(text)})
        except Exception as exc:
            print("dadata failed:", repr(exc))
            return self.send_json({"error": "Не удалось получить подсказки адреса"}, 502)

    def verified_address(self, raw):
        """Проверенный адрес для заказа. В демо-режиме (нет DaData) — без проверки."""
        if addr.demo_mode():
            value = str((raw or {}).get("value", "")).strip()[:300]
            postcode = re.sub(r"\D", "", str((raw or {}).get("postcode", "")))[:6]
            city = str((raw or {}).get("city", "")).strip()[:100]
            if not (value and city and len(postcode) == 6):
                raise ValueError("Укажите адрес, город и индекс")
            return {"value": value, "postcode": postcode, "city": city, "fias_id": "", "house": "", "verified": False}
        try:
            found = addr.verify(raw)
        except Exception as exc:
            print("dadata verify failed:", repr(exc))
            raise ValueError("Не удалось проверить адрес, попробуйте ещё раз")
        if not found:
            raise ValueError("Выберите адрес из списка подсказок — такого адреса мы не нашли")
        found["verified"] = True
        return found

    def api_delivery_quote(self):
        b = self.read_json()
        address = self.verified_address(b.get("address"))
        options = delivery.quote(address)
        if not options:
            raise ValueError("До этого адреса не удалось рассчитать доставку. Проверьте адрес.")
        self.send_json({"options": options, "address": address})

    def api_create_order(self):
        b = self.read_json()
        size = b.get("size", "10-15")
        raw_ids = b.get("generationIds") or [b.get("generationId", "")]
        gen_ids = [str(g) for g in raw_ids]
        metas = []
        for g in gen_ids:
            if not ID_RE.match(g) or not (GEN_DIR / g / "front.png").exists():
                raise ValueError("Макет не найден — создайте его заново")
            metas.append(json.loads((GEN_DIR / g / "meta.json").read_text(encoding="utf-8")))
        meta = metas[0]
        product = meta.get("product", "sculpture")
        if len(set(gen_ids)) != len(gen_ids) or any(m.get("product", "sculpture") != product for m in metas) \
                or any(m["style"] != meta["style"] for m in metas):
            raise ValueError("Некорректные макеты в заказе")
        if product == "growth" and len(gen_ids) != GROWTH_COUNT:
            raise ValueError(f"Для «Этапов взросления» нужно {GROWTH_COUNT} фото")
        if product == "sculpture" and len(gen_ids) != 1:
            raise ValueError("Некорректные макеты в заказе")
        gen_id = gen_ids[0]
        style, people, elements = meta["style"], meta["people"], meta["elements"]
        if style not in STYLES or size not in SIZES:
            raise ValueError("Некорректные параметры заказа")

        def field(name, label, required=True, maxlen=200):
            value = str(b.get(name, "")).strip()
            if required and not value:
                raise ValueError(f"Заполните поле «{label}»")
            return value[:maxlen]

        customer = {
            "name": field("name", "Имя"),
            "phone": field("phone", "Телефон", maxlen=30),
            "email": field("email", "Email"),
            "flat": field("flat", "Квартира", required=False, maxlen=20),
            "comment": field("comment", "Комментарий", required=False, maxlen=1000),
        }
        address = self.verified_address(b.get("address"))
        options = {o["id"]: o for o in delivery.quote(address)}
        option = options.get(str(b.get("deliveryId", "")))
        if not option:
            raise ValueError("Выберите способ доставки")
        customer.update(address=address["value"], city=address["city"], postcode=address["postcode"],
                        fias_id=address["fias_id"], address_verified=address["verified"])
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", customer["email"]):
            raise ValueError("Проверьте email")
        if len(re.sub(r"\D", "", customer["phone"])) < 10:
            raise ValueError("Проверьте телефон")
        if not b.get("consentOffer"):
            raise ValueError("Подтвердите согласие с публичной офертой")
        if not b.get("consentPd"):
            raise ValueError("Нужно согласие на обработку персональных данных")

        price = PRICE_GROWTH if product == "growth" else calc_price(people, elements)
        total = price + option["price"]
        order_id = uuid.uuid4().hex
        order = {
            "id": order_id, "created_at": int(time.time()), "status": "pending_payment",
            "generation_id": gen_id, "generation_ids": gen_ids, "labels": [m.get("label", "") for m in metas],
            "product": product, "style": style, "size": size,
            "people": people, "elements": elements, "element_note": meta.get("note", ""),
            "price": price, "shipping": option["price"], "total": total, "customer": customer,
            "delivery": option,
            # фиксируем, с какими редакциями документов согласился покупатель, и когда
            "consent": {"at": int(time.time()), "ip": self.client_ip(), "offer": DOCS_VERSION, "privacy": DOCS_VERSION},
        }
        return_url = f"{BASE_URL}/order/{order_id}"
        description = f"Статуэтка «Облик», {STYLES[style]}, {SIZES[size]}, {composition(people, elements, product)}"
        if yookassa.demo_mode():
            order["demo_payment"] = True
            confirmation_url = f"{BASE_URL}/pay-demo/{order_id}"
        else:
            payment = yookassa.create_payment(order, description, return_url)
            order["payment_id"] = payment["id"]
            confirmation_url = payment["confirmation"]["confirmation_url"]
        with lock:
            orders = read_orders()
            orders[order_id] = order
            write_orders(orders)
        self.send_json({"orderId": order_id, "confirmationUrl": confirmation_url})

    def api_webhook(self):
        event = self.read_json()
        payment_id = (event.get("object") or {}).get("id")
        if payment_id and not yookassa.demo_mode():
            # Не доверяем телу вебхука: перепроверяем платёж запросом к API.
            with lock:
                match = next((o for o in read_orders().values() if o.get("payment_id") == payment_id), None)
            if match:
                sync_payment(match)
        self.send_json({"ok": True})

    # --- админка
    def check_admin(self):
        if not ADMIN_PASSWORD:
            self.send_json({"error": "Админка отключена: задайте ADMIN_PASSWORD в .env"}, 404)
            return False
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                _, _, pwd = base64.b64decode(header[6:]).decode("utf-8").partition(":")
                if pwd == ADMIN_PASSWORD:
                    return True
            except Exception:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Oblik admin"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def admin_page(self):
        if not self.check_admin():
            return
        esc = html.escape
        rows = []
        for o in sorted(read_orders().values(), key=lambda x: -x["created_at"]):
            c = o["customer"]
            views = "".join(
                f"<div><b>{esc(g['label'])}</b> "
                f"<a href=\"/admin/original/{g['generation_id']}\" target=\"_blank\">исходное фото</a>"
                + "".join(f' · <a href="/admin/original/{g["generation_id"]}/{sd}" target="_blank">{lb}</a>'
                          for sd, lb in (("left", "слева"), ("right", "справа"), ("back", "сзади"))
                          if (GEN_DIR / g["generation_id"] / f"extra_{sd}.jpg").exists())
                + "<br>"
                + "".join(f'<a href="{u}" target="_blank"><img src="{u}" height="90"></a>' for u in g["views"])
                + "</div>" for g in order_groups(o))
            when = time.strftime("%d.%m.%Y %H:%M", time.localtime(o["created_at"]))
            rows.append(
                f"<tr><td>{when}<br><b>{esc(o['status'])}</b><br><small>{o['id'][:8]}</small></td>"
                f"<td>{esc(STYLES.get(o['style'], o['style']))}<br>{esc(SIZES.get(o['size'], o['size']))}<br>"
                f"{esc(composition(o.get('people', 1), o.get('elements', 0), o.get('product', 'sculpture')))}"
                f"{'<br><i>' + esc(o.get('element_note', '')) + '</i>' if o.get('element_note') else ''}<br>{o['total']} ₽</td>"
                f"<td>{esc(c['name'])}<br>{esc(c['phone'])}<br>{esc(c['email'])}<br>"
                f"{esc(c['postcode'])}, {esc(c['address'])}{', кв. ' + esc(c['flat']) if c.get('flat') else ''}"
                f"{'' if c.get('address_verified') else ' <b>(адрес не проверен)</b>'}<br>"
                f"Доставка: {esc(o.get('delivery', {}).get('name', '—'))}, {o['shipping']} ₽<br><i>{esc(c['comment'])}</i></td>"
                f"<td>{views}</td></tr>")
        page = (
            "<!doctype html><meta charset=utf-8><title>Заказы — Облик</title>"
            "<style>body{font:14px system-ui;margin:20px}td{border:1px solid #ccc;padding:8px;vertical-align:top}"
            "table{border-collapse:collapse}</style><h1>Заказы</h1><table>"
            + "".join(rows) + "</table>")
        self.send_bytes(page.encode("utf-8"), "text/html; charset=utf-8")


def parse_photo(data_url):
    m = re.fullmatch(r"data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)", data_url)
    if not m:
        raise ValueError("Загрузите фото в формате JPG, PNG или WebP")
    raw = base64.b64decode(m.group(2))
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("Файл слишком большой")
    if not (raw.startswith(b"\xff\xd8\xff") or raw.startswith(b"\x89PNG") or raw[8:12] == b"WEBP"):
        raise ValueError("Файл не похож на изображение")
    return raw


if __name__ == "__main__":
    print(f"Облик: {BASE_URL}")
    print("ИИ:", "ДЕМО-режим (нет ключа)" if ai.demo_mode() else "подключён")
    print("Адреса:", "ДЕМО-режим (нет DADATA_TOKEN, адрес не проверяется)" if addr.demo_mode() else "DaData")
    print("Доставка:", "ДЕМО (фиксированная цена)" if delivery.demo_mode() else "СДЭК")
    print("Оплата:", "ДЕМО-режим (нет ключей ЮKassa)" if yookassa.demo_mode() else "ЮKassa")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
