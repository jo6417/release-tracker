# -*- coding: utf-8 -*-
"""ITAD 가격을 노션 작품 DB에 반영한다.

외부ID에 steam:appid가 있는 게임이 대상이다.
정가·현재최저가·역대최저가를 채우고, 목표가에 도달했으면 알린다.

알림 대상은 `진행도(게임)`이 `구매 대기`나 `출시 대기`인 것뿐이다.

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

# 할인을 알릴 진행도. 살 마음이 있다고 사람이 표시해둔 것만 대상이다.
#
# 예전에는 `소유처`가 비어 있으면 미보유로 봤는데, 소유처는 채우다 만 칸이라
# 이미 졸업한 게임 112건이 전부 "미보유"로 통과했다(2026-08-21, 엔세스터가
# "→ 미보유 · PS4"로 추천됨). 보유 여부는 소유처가 아니라 진행도가 안다.
WANT = ("구매 대기", "출시 대기")


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
        st = pr.get("진행도(게임)", {}).get("select")
        stage = st["name"] if st else None
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
        hit = now_hit and not was_hit and stage in WANT and prev is not None
        if hit:
            plats = ([o["name"] for o in pr["플랫폼"]["multi_select"]]
                     if "플랫폼" in pr else [])
            lines = [f"{d['할인율']}% 할인 — {d['현재최저가']:,.0f}원 "
                     f"(정가 {d['정가']:,.0f}원) @ {d['상점']}"]
            low = lows.get(gid)
            if low:
                gap = d["현재최저가"] - low
                lines.append(f"역대최저 {low:,.0f}원"
                             + (" — 지금이 역대최저" if gap <= 0
                                else f" (지금은 {gap:,.0f}원 비쌈)"))
            lines.append(f"→ {stage}" + (f" · {'/'.join(plats)}" if plats else ""))
            alerts.append((title,
                           f"{title} {d['할인율']}% {d['현재최저가']:,.0f}원",
                           lines))

        n += 1
        if a.dry:
            if n <= 10 or hit:
                mark = " ★목표가" if hit else ""
                print(f"  {title[:26]:26} {d['현재최저가']:>8,.0f}원 "
                      f"(-{d['할인율']}%) 역대 {lows.get(gid, 0):>8,.0f}원"
                      f"  [{stage}]{mark}")
        else:
            patch(page["id"], props)
            time.sleep(0.34)

    print(f"\n{'갱신 예정' if a.dry else '갱신'} {n}건 / 목표가 도달 {len(alerts)}건")
    for _, short, _ in alerts[:15]:
        print("   " + short)
    if alerts and not a.dry:
        # 할인도 카드 한 장으로 묶는다. 낱개로 보내면 세일 기간에 알림이 쏟아진다.
        notify.send_card(
            f"목표가 도달 {len(alerts)}건",
            summary=["[할인] " + ", ".join(t for t, _, _ in alerts)],
            details=[(f"[할인] {t}", lines) for t, _, lines in alerts],
            kinds=["할인"], count=len(alerts))
        print("알림 카드 1장 발송 완료")


if __name__ == "__main__":
    main()
