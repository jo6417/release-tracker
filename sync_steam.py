# -*- coding: utf-8 -*-
"""스팀 보유·플레이 기록을 노션 작품 DB에 반영한다.

steam_map.json(appid → 작품 제목)을 기준으로 매칭한다.
스팀은 영문, 노션은 한글이라 자동 매칭이 안 되므로 이 표가 정본이다.

단계 판정:
    플레이 0시간이고 진행도가 `미확인` → 보유함 (샀지만 아직 안 함)
    그 밖의 모든 경우                  → 진행도는 건드리지 않는다. 플레이 기록만 갱신한다

    진행도가 지금 무엇이든 사람이 정한 값이다. 스팀이 말해줄 수 있는 건
    "샀다"와 "몇 시간 켰다"까지고, 그게 클리어인지 접은 건지는 말해주지 않는다.

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

    done = {"갱신": 0, "보유함": 0, "누락": []}
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
        # 자동으로 올리는 진행도는 이것 하나뿐이다.
        #
        # 예전에는 "최근 90일 안에 플레이 기록이 있으면 진행 중"도 있었다.
        # 플레이 기록은 과거형인데 `진행 중`은 현재형이라 둘은 같은 말이 아니다.
        # 253시간을 하고 졸업한 사람과 지금 붙잡고 있는 사람이 스팀 API에서는
        # 구분되지 않는다. 그래서 사람이 찍어둔 졸업·일시 중단·보유함을 매일
        # 아침 진행 중으로 되돌렸고, 변경이력만 남고 값은 사라졌다(2026-08-19,
        # 6건). 규칙을 예외로 감싸는 대신 규칙 자체를 뺐다.
        new_stage = None
        if g["플레이분"] == 0 and stage == "미확인":
            new_stage = "보유함"
            props["진행도(게임)"] = {"select": {"name": new_stage}}
            done["보유함"] += 1

        if a.dry:
            # 진행도가 어떻게 될 예정인지 찍는다. 이게 없으면 덮어쓰기가
            # 일어나는지를 노션을 열어보기 전에는 알 수 없다.
            h = g["플레이분"] // 60
            move = f"{stage} → {new_stage}" if new_stage else f"{stage} 유지"
            print(f"  {title:26} {h:>4}시간  진행도 {move:16} 소유처={owners}")
        else:
            patch_page(page["id"], props)
            time.sleep(0.34)
        done["갱신"] += 1

    print(f"\n갱신 {done['갱신']}건 (진행도 변경 {done['보유함']}건: 미확인 → 보유함)")
    if done["누락"]:
        print("매칭 실패:", done["누락"])
    if a.dry:
        print("--dry 모드: 노션 미변경")


if __name__ == "__main__":
    main()
