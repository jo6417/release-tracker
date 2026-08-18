# -*- coding: utf-8 -*-
"""스팀에만 있고 노션에 없는 게임을 작품 DB에 추가한다.

steam_add_candidates.json(플레이 1시간 이상, 도구·테스트빌드 제외)을 읽어
작품 행을 만들고, steam_map.json에 매핑을 덧붙인다.

제목은 스팀의 영문 이름을 그대로 쓴다. 한글화는 나중에 IGDB로.

사용법:
    python add_steam_games.py --dry
    python add_steam_games.py
"""
import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.request

from config import API, headers

IDS_FILE = "db_ids.json"
MAP_FILE = "steam_map.json"
CAND_FILE = "steam_add_candidates.json"
RECENT_DAYS = 90


def post(path, payload):
    req = urllib.request.Request(f"{API}{path}", data=json.dumps(payload).encode(),
                                 headers=headers(), method="POST")
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    with io.open(CAND_FILE, encoding="utf-8") as f:
        cands = json.load(f)
    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)
    with io.open(MAP_FILE, encoding="utf-8") as f:
        mapping = json.load(f)

    exist = set()
    for p in query_all(ids["work_db"]):
        exist.add("".join(x["plain_text"] for x in p["properties"]["제목"]["title"]))

    today = time.strftime("%Y-%m-%d")
    now = time.time()
    added = 0
    for g in cands:
        if g["이름"] in exist:
            continue
        recent = g["최근플레이"] and (now - g["최근플레이"]) < RECENT_DAYS * 86400
        props = {
            "제목": {"title": [{"type": "text", "text": {"content": g["이름"][:1900]}}]},
            "종류": {"select": {"name": "게임"}},
            "진행도(게임)": {"select": {"name": "진행 중" if recent else "미확인"}},
            "플랫폼": {"multi_select": [{"name": "PC"}]},
            "소유처": {"multi_select": [{"name": "스팀"}]},
            "획득경로": {"multi_select": [{"name": "구매"}]},
            "플레이시간": {"number": round(g["플레이분"] / 60, 1)},
            "외부ID": {"rich_text": [{"type": "text",
                                    "text": {"content": f"steam:{g['appid']}"}}]},
            "레퍼런스": {"url": f"https://store.steampowered.com/app/{g['appid']}"},
            "날짜정밀도": {"select": {"name": "미정"}},
            "변경이력": {"rich_text": [{"type": "text",
                                    "text": {"content": f"{today}: 스팀 라이브러리에서 추가"}}]},
        }
        if g["최근플레이"]:
            props["마지막플레이일"] = {"date": {
                "start": time.strftime("%Y-%m-%d", time.localtime(g["최근플레이"]))}}

        if a.dry:
            print(f"  + {g['이름'][:44]:44} {g['플레이분']//60:>4}h "
                  f"{'진행 중' if recent else '미확인'}")
        else:
            post("/pages", {"parent": {"database_id": ids["work_db"]},
                            "properties": props})
            time.sleep(0.34)
        mapping[str(g["appid"])] = g["이름"]
        added += 1

    if not a.dry:
        with io.open(MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=1)
    print(f"\n{'추가 예정' if a.dry else '추가됨'}: {added}건"
          f"{' (steam_map.json 갱신)' if not a.dry else ''}")


if __name__ == "__main__":
    main()
