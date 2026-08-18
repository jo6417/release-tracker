# -*- coding: utf-8 -*-
"""ITAD 가격을 노션 작품 DB에 반영한다.

외부ID에 steam:appid가 있는 게임이 대상이다.
정가·현재최저가·역대최저가를 채우고, 목표가에 도달했으면 알린다.

알림에는 반드시 소유 여부를 함께 싣는다. 중복 구매 방지가 목적이다.

사용법:
    python sync_prices.py --dry
    python sync_prices.py
"""
import argparse
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request

import notify
from adapters import igdb, itad
from config import API, headers

IDS_FILE = "db_ids.json"
DEFAULT_TARGET = 0.30      # 할인 알림 기준. 작품별 조정은 두지 않는다


def query_all(dbid):
    out, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        req = urllib.request.Request(f"{API}/databases/{dbid}/query",
                                     data=json.dumps(body).encode(),
                                     headers=headers(), method="POST")
        with urllib.request.urlopen(req) as r:
            d = json.loads(r.read())
        out += d["results"]
        if not d.get("has_more"):
            return out
        cursor = d["next_cursor"]


def patch(pid, props):
    req = urllib.request.Request(f"{API}/pages/{pid}",
                                 data=json.dumps({"properties": props}).encode(),
                                 headers=headers(), method="PATCH")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt)
                continue
            print(f"[에러] {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
            raise
    raise RuntimeError("재시도 초과")


def txt(prop):
    return "".join(x["plain_text"] for x in prop[prop["type"]])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)

    targets = {}          # appid -> page
    need_igdb = {}        # igdb_id -> page (스팀 appid를 모르는 게임)
    for p in query_all(ids["work_db"]):
        kind = p["properties"]["종류"]["select"]
        if not kind or kind["name"] != "게임":
            continue
        ext = txt(p["properties"]["외부ID"])
        m = re.search(r"steam:(\d+)", ext)
        if m:
            targets[m.group(1)] = p
            continue
        g = re.search(r"igdb:(\d+)", ext)
        if g:
            need_igdb[int(g.group(1))] = p
    print(f"스팀 appid 보유 {len(targets)}건 / IGDB로 찾을 대상 {len(need_igdb)}건")

    # 미보유 게임도 가격을 보려면 appid가 필요하다 — IGDB에서 역으로 얻는다
    if need_igdb:
        found = igdb.steam_appids(need_igdb)
        for gid, appid in found.items():
            targets.setdefault(appid, need_igdb[gid])
        print(f"  IGDB로 appid {len(found)}건 확보 → 총 {len(targets)}건")

    appid_to_itad = itad.ids_from_steam(list(targets))
    print(f"  ITAD 매칭 {len(appid_to_itad)}건")
    price = itad.prices(appid_to_itad.values())
    lows = itad.history_low(appid_to_itad.values())
    print(f"  가격 조회 {len(price)}건")

    alerts, n = [], 0
    for appid, page in targets.items():
        gid = appid_to_itad.get(appid)
        d = price.get(gid) if gid else None
        if not d:
            continue
        pr = page["properties"]
        title = txt(pr["제목"])
        owners = [o["name"] for o in pr["소유처"]["multi_select"]]
        target_rate = DEFAULT_TARGET

        props = {
            "정가": {"number": round(d["정가"])},
            "현재최저가": {"number": round(d["현재최저가"])},
            "마지막확인": {"date": {"start": time.strftime("%Y-%m-%d")}},
        }
        if gid in lows:
            props["역대최저가"] = {"number": round(lows[gid])}

        # 알림은 '새로' 목표가에 들어왔을 때만 보낸다.
        # 노션에 기록된 이전 최저가가 목표 위였다가 아래로 내려온 경우만.
        # 첫 조회(이전 값 없음)는 기준값만 잡고 조용히 넘어간다.
        prev = pr["현재최저가"]["number"]
        limit = d["정가"] * (1 - target_rate) if d["정가"] else 0
        now_hit = bool(limit) and d["현재최저가"] <= limit
        was_hit = prev is not None and prev <= limit
        hit = now_hit and not was_hit and not owners and prev is not None
        if hit:
            line = (f"[할인] {title} {d['할인율']}% "
                    f"({d['현재최저가']:,.0f}원 / 정가 {d['정가']:,.0f}원) @ {d['상점']}")
            low = lows.get(gid)
            if low:
                line += f" / 역대최저 {low:,.0f}원"
            alerts.append(line)

        n += 1
        if a.dry:
            if n <= 10 or hit:
                mark = " ★목표가" if hit else ""
                own = f" [보유: {'/'.join(owners)}]" if owners else ""
                print(f"  {title[:26]:26} {d['현재최저가']:>8,.0f}원 "
                      f"(-{d['할인율']}%) 역대 {lows.get(gid, 0):>8,.0f}원{mark}{own}")
        else:
            patch(page["id"], props)
            time.sleep(0.34)

    print(f"\n{'갱신 예정' if a.dry else '갱신'} {n}건 / 목표가 도달 {len(alerts)}건")
    for line in alerts[:15]:
        print("   " + line.replace("\n", " "))
    if alerts and not a.dry:
        for line in alerts:
            notify.send(line)
        print("알림 발송 완료")


if __name__ == "__main__":
    main()
