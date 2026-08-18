# -*- coding: utf-8 -*-
"""스팀 보유·플레이 기록을 노션 작품 DB에 반영한다.

steam_map.json(appid → 작품 제목)을 기준으로 매칭한다.
스팀은 영문, 노션은 한글이라 자동 매칭이 안 되므로 이 표가 정본이다.

단계 판정:
    플레이 0시간            → 보유함 (샀지만 아직 안 함)
    플레이 있고 90일 이내    → 진행 중
    플레이 있고 그보다 오래됨 → 진행도는 그대로 두고 기록만 (클리어인지 폐기됨(노잼)인지는 사람이 판단)

사용법:
    python sync_steam.py --dry    # 미리보기
    python sync_steam.py
"""
import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.request

from config import API, headers
from adapters import steam

IDS_FILE = "db_ids.json"
MAP_FILE = "steam_map.json"
VANITY = "jo6417"
RECENT_DAYS = 90


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


def patch_page(pid, props):
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


def text_of(prop):
    return "".join(x["plain_text"] for x in prop.get(prop["type"], []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="노션에 쓰지 않고 결과만 출력")
    a = ap.parse_args()

    with io.open(MAP_FILE, encoding="utf-8") as f:
        mapping = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)

    lib = {str(g["appid"]): g for g in steam.owned_games(steam.steam_id(VANITY))}
    print(f"스팀 보유 {len(lib)}개 / 매핑 {len(mapping)}건")

    pages = {}
    for p in query_all(ids["work_db"]):
        kind = p["properties"]["종류"]["select"]
        if kind and kind["name"] == "게임":
            pages[text_of(p["properties"]["제목"])] = p

    now = time.time()
    done = {"갱신": 0, "보유함": 0, "진행 중": 0, "누락": []}
    for appid, title in mapping.items():
        page = pages.get(title)
        g = lib.get(appid)
        if not page or not g:
            done["누락"].append(title)
            continue

        owners = [o["name"] for o in page["properties"]["소유처"]["multi_select"]]
        if "스팀" not in owners:
            owners.append("스팀")
        routes = [o["name"] for o in page["properties"]["획득경로"]["multi_select"]]
        if "구매" not in routes:
            routes.append("구매")

        props = {
            "소유처": {"multi_select": [{"name": v} for v in owners]},
            "획득경로": {"multi_select": [{"name": v} for v in routes]},
            "플레이시간": {"number": round(g["플레이분"] / 60, 1)},
            "외부ID": {"rich_text": [{"type": "text",
                                    "text": {"content": f"steam:{appid}"}}]},
        }
        if g["최근플레이"]:
            last = time.strftime("%Y-%m-%d", time.localtime(g["최근플레이"]))
            props["마지막플레이일"] = {"date": {"start": last}}

        stage = page["properties"]["진행도(게임)"]["select"]
        stage = stage["name"] if stage else None
        if g["플레이분"] == 0:
            if stage == "미확인":
                props["진행도(게임)"] = {"select": {"name": "보유함"}}
                done["보유함"] += 1
        elif g["최근플레이"] and (now - g["최근플레이"]) < RECENT_DAYS * 86400:
            props["진행도(게임)"] = {"select": {"name": "진행 중"}}
            done["진행 중"] += 1

        if a.dry:
            h = g["플레이분"] // 60
            print(f"  {title:26} {h:>4}시간  소유처={owners}")
        else:
            patch_page(page["id"], props)
            time.sleep(0.34)
        done["갱신"] += 1

    print(f"\n갱신 {done['갱신']}건 (보유함 {done['보유함']}, 진행 중 {done['진행 중']})")
    if done["누락"]:
        print("매칭 실패:", done["누락"])
    if a.dry:
        print("--dry 모드: 노션 미변경")


if __name__ == "__main__":
    main()
