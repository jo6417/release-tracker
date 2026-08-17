# -*- coding: utf-8 -*-
"""IGDB 매칭 결과를 노션 작품 DB에 반영한다.

비어 있는 칸만 채운다. 덕근님이 직접 넣은 값은 건드리지 않는다.
  외부ID   igdb:1234 추가 (steam:5678은 유지)
  원제     IGDB 영문명
  플랫폼   비어 있을 때만 (IGDB는 '출시된 플랫폼'이지 '보유'가 아님)
  평점     IGDB 종합 평점
  출시일   비어 있을 때만. 채우면 정밀도도 '확정'으로

사용법:
    python apply_igdb.py --dry
    python apply_igdb.py
"""
import argparse
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request

from config import API, headers

IDS_FILE = "db_ids.json"
MATCH = "igdb_match.json"

# 확신 '낮음'이지만 사람이 보고 맞다고 판단한 것들
# (IGDB 표기가 달라 자동 판정에서 걸린 경우: GTA6 → Grand Theft Auto VI 등)
ACCEPT_LOW = {
    "GTA6", "GTA5", "포켓몬스터 ZA", "포코피아", "팬텀블레이드 제로",
    "Master Of Albion", "아웃라스트2", "포켓몬스터 소드&실드", "메트로:어웨이크닝",
}

PLATFORM_MAP = {
    "PC": "PC", "PS5": "PS5", "PS4": "PS4", "PS3": "PS3",
    "Switch": "Switch", "Switch 2": "Switch2",
    "XONE": "Xbox", "Series X|S": "Xbox", "X360": "Xbox", "XBOX": "Xbox",
}


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

    with io.open(MATCH, encoding="utf-8") as f:
        match = json.load(f)
    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)

    stat = {"외부ID": 0, "원제": 0, "플랫폼": 0, "평점": 0, "출시일": 0}
    n = 0
    for p in query_all(ids["work_db"]):
        props = p["properties"]
        kind = props["종류"]["select"]
        if not kind or kind["name"] != "게임":
            continue
        title = txt(props["제목"])
        m = match.get(title)
        if not m or (m.get("확신") == "낮음" and title not in ACCEPT_LOW):
            continue

        new = {}
        ext = txt(props["외부ID"])
        if f"igdb:{m['id']}" not in ext:
            joined = (ext + " " if ext else "") + f"igdb:{m['id']}"
            new["외부ID"] = {"rich_text": [{"type": "text",
                                          "text": {"content": joined[:1900]}}]}
            stat["외부ID"] += 1
        if not txt(props["원제"]) and m.get("이름"):
            new["원제"] = {"rich_text": [{"type": "text",
                                       "text": {"content": m["이름"][:1900]}}]}
            stat["원제"] += 1
        if not props["플랫폼"]["multi_select"] and m.get("플랫폼"):
            vals = []
            for x in m["플랫폼"]:
                v = PLATFORM_MAP.get(x)
                if v and v not in vals:
                    vals.append(v)
            if vals:
                new["플랫폼"] = {"multi_select": [{"name": v} for v in vals]}
                stat["플랫폼"] += 1
        if props["평점"]["number"] is None and m.get("평점"):
            new["평점"] = {"number": m["평점"]}
            stat["평점"] += 1
        if not props["출시·개봉일"]["date"] and m.get("출시일"):
            new["출시·개봉일"] = {"date": {"start": m["출시일"]}}
            new["날짜정밀도"] = {"select": {"name": "확정"}}
            stat["출시일"] += 1
        if not props["레퍼런스"]["url"] and m.get("url"):
            new["레퍼런스"] = {"url": m["url"]}

        if not new:
            continue
        n += 1
        if a.dry:
            if n <= 12:
                print(f"  {title:28} {list(new)}")
        else:
            patch(p["id"], new)
            time.sleep(0.34)

    print(f"\n{'갱신 예정' if a.dry else '갱신'} {n}건: {stat}")


if __name__ == "__main__":
    main()
