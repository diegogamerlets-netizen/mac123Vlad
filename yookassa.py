"""Минимальный клиент ЮKassa (API v3). Без ключей работает демо-оплата."""
import base64
import json
import os
import urllib.request
import uuid

API = "https://api.yookassa.ru/v3"


def demo_mode():
    return not (os.environ.get("YOOKASSA_SHOP_ID") and os.environ.get("YOOKASSA_SECRET_KEY"))


def _request(method, path, payload=None, idempotence_key=None):
    auth = base64.b64encode(
        f"{os.environ['YOOKASSA_SHOP_ID']}:{os.environ['YOOKASSA_SECRET_KEY']}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
    if idempotence_key:
        headers["Idempotence-Key"] = idempotence_key
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _amount(value):
    return {"value": f"{value:.2f}", "currency": "RUB"}


def create_payment(order, description, return_url):
    items = [{
        "description": description[:128], "quantity": "1.00", "amount": _amount(order["price"]),
        "vat_code": int(os.environ.get("YOOKASSA_VAT_CODE", "1")),
        "payment_mode": "full_payment", "payment_subject": "commodity",
    }]
    if order["shipping"]:
        items.append({
            "description": "Доставка", "quantity": "1.00", "amount": _amount(order["shipping"]),
            "vat_code": int(os.environ.get("YOOKASSA_VAT_CODE", "1")),
            "payment_mode": "full_payment", "payment_subject": "service",
        })
    payload = {
        "amount": _amount(order["total"]),
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": f"Заказ {order['id'][:8]}: {description}"[:128],
        "metadata": {"order_id": order["id"]},
    }
    # Чек (54-ФЗ). Если онлайн-касса в ЮKassa не подключена — YOOKASSA_RECEIPT=0.
    if os.environ.get("YOOKASSA_RECEIPT", "1") != "0":
        payload["receipt"] = {
            "customer": {"full_name": order["customer"]["name"], "email": order["customer"]["email"]},
            "items": items,
        }
        tax_system = os.environ.get("YOOKASSA_TAX_SYSTEM")
        if tax_system:
            payload["receipt"]["tax_system_code"] = int(tax_system)
    return _request("POST", "/payments", payload, idempotence_key=order["id"])


def get_payment(payment_id):
    return _request("GET", f"/payments/{payment_id}")
