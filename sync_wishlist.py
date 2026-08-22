# -*- coding: utf-8 -*-
"""스팀 위시리스트 → 후보 큐.

DB에 없으면 아무것도 못 한다는 것이 이 시스템의 가장 큰 약점인데, 위시리스트는
사용자가 이미 "이거 하고 싶다"고 찍어둔 목록이라 가장 싼 재료다. 새로 만들
판단 기준이 없다 — 찜한 것 자체가 판단이다.

작품 DB에 이미 있는 것은 뺀다. 판정은 IGDB id로 한다. 제목 문자열로 맞추면
한/영과 에디션 접미사 때문에 같은 게임이 새 후보로 또 올라온다.

사용법:
    python sync_wishlist.py --dry
    python sync_wishlist.py
"""
import argparse
import io
import json
import re
import time
import urllib.request

import candidates
from adapters import igdb, steam
from config import API, headers

IDS_FILE = "db_ids.json"
VANITY = "jo6417"


def query_all(dbid):
    out, cur = [], None
    while True:
        body = {"page_size": 100}
        if cur:
            body["start_cursor"] = cur
        req = urllib.request.Request(f"{API}/databases/{dbid}/query",
                                     data=json.dumps(body).encode(),
                                     headers=headers(), method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
        out += d["results"]
        if not d.get("has_more"):
            return out
        cur = d["next_cursor"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)

    items = steam._get("/IWishlistService/GetWishlist/v1/",
                       steamid=steam.steam_id(VANITY))
    appids = [str(x["appid"]) for x in items.get("response", {}).get("items", [])]
    print("위시리스트 %d건" % len(appids))
    if not appids:
        return

    # appid → IGDB id. 여기서 안 잡히는 것은 IGDB에 없는 게임이라 그냥 둔다.
    igdb_of = igdb.by_steam_appids(appids)
    detail = igdb.games(set(igdb_of.values()))
    print("  IGDB 매칭 %d건" % len(igdb_of))

    있음 = set()
    for p in query_all(ids["work_db"]):
        ext = "".join(x["plain_text"] for x in p["properties"]["외부ID"]["rich_text"])
        m = re.search("igdb:([0-9]+)", ext)
        if m:
            있음.add(int(m.group(1)))
        m = re.search("steam:([0-9]+)", ext)
        if m:
            있음.add("steam:" + m.group(1))

    새후보 = []
    for appid in appids:
        gid = igdb_of.get(appid)
        if gid in 있음 or ("steam:" + appid) in 있음:
            continue
        g = detail.get(gid, {})
        제목 = g.get("이름") or ""
        if not 제목:
            continue
        새후보.append({
            "키": "steam:" + appid,
            "제목": 제목,
            "종류": "게임",
            "출처": "스팀 위시리스트",
            # 큐 카드에 붙일 한 줄. 제목만 던지면 그냥 넘기게 된다.
            "설명": " · ".join(x for x in [
                "/".join(g.get("플랫폼") or [])[:24],
                ("평점 %s" % g["평점"]) if g.get("평점") else "",
                ("출시 %s" % g["출시일"]) if g.get("출시일") else "미출시",
                ("시리즈: " + g["시리즈"][0]) if g.get("시리즈") else "",
            ] if x),
            "레퍼런스": "https://store.steampowered.com/app/" + appid,
            "외부ID": "igdb:%d steam:%s" % (gid, appid),
        })

    state = candidates.load()
    added = candidates.add(state, 새후보)
    print("  DB에 없는 것 %d건 / 큐에 새로 넣은 것 %d건" % (len(새후보), len(added)))
    for c in added[:10]:
        print("   %s %s" % (c["번호"], c["제목"][:40]))
    if not a.dry:
        candidates.save(state)


if __name__ == "__main__":
    main()
