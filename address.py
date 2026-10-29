"""Подсказки и проверка адреса через DaData (Подсказки по адресам).

Без DADATA_TOKEN работает демо-режим: адрес вводится вручную и НЕ проверяется.
"""
import json
import os
import urllib.request

URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/suggest/address"


def demo_mode():
    return not os.environ.get("DADATA_TOKEN")


def _query(text, count):
    body = json.dumps({
        "query": text, "count": count,
        "from_bound": {"value": "city"}, "to_bound": {"value": "house"},
    }).encode("utf-8")
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "Authorization": "Token " + os.environ["DADATA_TOKEN"],
    })
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read()).get("suggestions", [])


def _simplify(s):
    d = s.get("data") or {}
    return {
        "value": s.get("unrestricted_value") or s.get("value"),
        "fias_id": d.get("fias_id") or "",
        "postcode": d.get("postal_code") or "",
        "city": d.get("city") or d.get("settlement") or d.get("area") or "",
        "region": d.get("region_with_type") or "",
        "house": d.get("house") or "",
        "street": d.get("street") or "",
    }


def suggest(text, count=7):
    return [_simplify(s) for s in _query(text, count)]


def verify(address):
    """Подтверждает, что адрес реально существует и доведён до номера дома.

    Клиенту не доверяем: ещё раз ищем строку адреса в DaData и сверяем идентификатор ФИАС.
    Возвращает нормализованный адрес или None.
    """
    value = str((address or {}).get("value", "")).strip()
    fias_id = str((address or {}).get("fias_id", "")).strip()
    if not value or not fias_id:
        return None
    for item in suggest(value, count=5):
        if item["fias_id"] == fias_id and item["house"]:
            return item
    return None
