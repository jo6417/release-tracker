# -*- coding: utf-8 -*-
"""IsThereAnyDeal 어댑터 — 가격 조회.

SteamDB는 스크래핑이 금지라 가격은 ITAD로 받는다.
ITAD는 40여 개 상점을 주지만 이 어댑터는 스팀 것만 골라 돌려준다.
사용자가 스팀에서만 사기 때문이다 — 해외 키 리셀러 특가는 알림에 뜨면 안 된다.
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
    """{itad_id: {현재최저가, 정가, 할인율, 상점}} — 스팀 가격만.

    shops로 상점을 좁혀서 받는다. 스팀에 안 파는 게임(미출시·삭제·국가 미판매)은
    응답에 아예 없으므로 호출한 쪽에서 조용히 빠진다.

    `deals=true`는 쓰지 않는다. 그건 '할인 중인 것만' 이라는 뜻이라 정가로 파는
    스팀 가격이 통째로 빠지고, 그러면 세일이 끝나도 노션의 현재최저가가 세일가에
    멈춰 있게 된다. 할인 여부와 무관하게 지금 스팀 가격을 받아야 한다.
    """
    out = {}
    ids = list(itad_ids)
    for i in range(0, len(ids), 200):
        for g in _call("/games/prices/v3", ids[i:i + 200],
                       country=COUNTRY, shops=STEAM_SHOP, capacity=0) or []:
            deals = [d for d in (g.get("deals") or [])
                     if d["shop"]["id"] == STEAM_SHOP]
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
    """{itad_id: 역대최저가} — 스팀 기준.

    `/games/historylow/v1`은 shops를 줘도 무시하고 전 상점 최저를 돌려준다
    (2026-08-22 확인: GTA V가 WinGameStore, 엘든 링이 2game으로 나왔다).
    스팀 현재가와 나란히 놓으려면 상점별 최저를 주는 storelow를 써야 한다.
    """
    out = {}
    ids = list(itad_ids)
    for i in range(0, len(ids), 200):
        for g in _call("/games/storelow/v2", ids[i:i + 200],
                       country=COUNTRY, shops=STEAM_SHOP) or []:
            for low in g.get("lows") or []:
                if low["shop"]["id"] == STEAM_SHOP and low.get("price"):
                    out[g["id"]] = low["price"]["amount"]
                    break
    return out
