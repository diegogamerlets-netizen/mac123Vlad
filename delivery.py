"""Расчёт доставки. Каждая служба — отдельная функция, возвращающая список вариантов.

Сейчас подключён СДЭК (API v2, расчёт по тарифам) и запасной вариант с фиксированной ценой.
Чтобы добавить Яндекс Доставку, Почту России и др., реализуйте функцию вида
`_xxx_options(address) -> [{id, provider, name, price, days}]` и добавьте её в PROVIDERS.
"""
import json
import os
import time
import urllib.parse
import urllib.request

_token = {"value": None, "exp": 0}


def _cdek_enabled():
    return bool(os.environ.get("CDEK_CLIENT_ID") and os.environ.get("CDEK_CLIENT_SECRET")
                and os.environ.get("CDEK_FROM_POSTCODE"))


def demo_mode():
    return not _cdek_enabled()


def _cdek_base():
    return os.environ.get("CDEK_API_URL", "https://api.cdek.ru/v2").rstrip("/")


def _cdek_call(path, payload=None, form=None):
    headers, data = {}, None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    else:
        headers["Authorization"] = "Bearer " + _cdek_token()
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(_cdek_base() + path, data=data, headers=headers,
                                 method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def _cdek_token():
    if _token["value"] and _token["exp"] > time.time() + 60:
        return _token["value"]
    data = _cdek_call("/oauth/token?parameters", form={
        "grant_type": "client_credentials",
        "client_id": os.environ["CDEK_CLIENT_ID"],
        "client_secret": os.environ["CDEK_CLIENT_SECRET"],
    })
    _token.update(value=data["access_token"], exp=time.time() + int(data.get("expires_in", 3600)))
    return _token["value"]


def _cdek_options(address):
    pkg = {
        "weight": int(os.environ.get("PACKAGE_WEIGHT_G", "500")),
        "length": int(os.environ.get("PACKAGE_LENGTH_CM", "20")),
        "width": int(os.environ.get("PACKAGE_WIDTH_CM", "20")),
        "height": int(os.environ.get("PACKAGE_HEIGHT_CM", "25")),
    }
    payload = {
        "type": 1,
        "from_location": {"postal_code": os.environ["CDEK_FROM_POSTCODE"], "country_code": "RU"},
        "to_location": {"postal_code": address["postcode"], "city": address["city"], "country_code": "RU"},
        "packages": [pkg],
    }
    result = _cdek_call("/calculator/tarifflist", payload)
    # delivery_mode: 1 дверь-дверь, 3 склад-дверь — доставка курьером до адреса получателя
    door = [t for t in result.get("tariff_codes", []) if t.get("delivery_mode") in (1, 3)]
    if not door:
        return []
    best = min(door, key=lambda t: t["delivery_sum"])
    extra = int(os.environ.get("DELIVERY_EXTRA_RUB", "0"))
    lo, hi = best.get("period_min"), best.get("period_max")
    return [{
        "id": f"cdek:{best['tariff_code']}", "provider": "СДЭК",
        "name": "Курьером до двери (СДЭК)",
        "price": int(round(best["delivery_sum"])) + extra,
        "days": f"{lo}–{hi} дн." if lo and hi and lo != hi else (f"{lo} дн." if lo else ""),
    }]


def _flat_options(address):
    return [{
        "id": "flat", "provider": "Демо", "name": "Доставка по России (демо-расчёт)",
        "price": int(os.environ.get("DELIVERY_FLAT_RUB", "500")), "days": "",
    }]


def quote(address):
    """Возвращает варианты доставки до проверенного адреса."""
    if _cdek_enabled():
        try:
            options = _cdek_options(address)
            if options:
                return options
        except Exception as exc:
            print("CDEK quote failed:", repr(exc))
        return []
    return _flat_options(address)
