# -*- coding: utf-8 -*-
"""IsThereAnyDeal 어댑터 — 가격 조회.

SteamDB는 스크래핑이 금지라 가격은 ITAD로 받는다.
40여 개 상점의 현재 최저가와 역대 최저가를 준다.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.isthereanydeal.com"
STEAM_SHOP = 61
COUNTRY = "KR"
_last = [0.0]


def _call(path, payload=None, **params):
    params["key"] = os.environ.get("ITAD_API_KEY", "")
    url = f"{API}{path}?" + urllib.parse.urlencode(params)
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method="POST" if data else "GET",
        headers={"Content-Type": "application/json",
                 "User-Agent": "release-tracker"})
    wait = 0.2 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                _last[0] = time.time()
                body = r.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503):
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError("ITAD 재시도 초과")


def ids_from_steam(appids):
    """스팀 appid 목록 → {appid: itad_id}"""
    out = {}
    appids = [str(a) for a in appids]
    for i in range(0, len(appids), 200):
        chunk = appids[i:i + 200]
        res = _call(f"/lookup/id/shop/{STEAM_SHOP}/v1",
                    [f"app/{a}" for a in chunk]) or {}
        for k, v in res.items():
            if v:
                out[k.split("/", 1)[1]] = v
    return out


def prices(itad_ids):
    """{itad_id: {현재최저가, 정가, 할인율, 상점}}"""
    out = {}
    ids = list(itad_ids)
    for i in range(0, len(ids), 200):
        for g in _call("/games/prices/v3", ids[i:i + 200],
                       country=COUNTRY, deals="true", nondeals="true") or []:
            deals = g.get("deals") or []
            if not deals:
                continue
            best = min(deals, key=lambda d: d["price"]["amount"])
            out[g["id"]] = {
                "현재최저가": best["price"]["amount"],
                "정가": best["regular"]["amount"],
                "할인율": best.get("cut", 0),
                "상점": best["shop"]["name"],
                "링크": best.get("url"),
            }
    return out


def history_low(itad_ids):
    """{itad_id: 역대최저가}"""
    out = {}
    ids = list(itad_ids)
    for i in range(0, len(ids), 200):
        for g in _call("/games/historylow/v1", ids[i:i + 200],
                       country=COUNTRY) or []:
            low = g.get("low") or {}
            if low.get("price"):
                out[g["id"]] = low["price"]["amount"]
    return out
