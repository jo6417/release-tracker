# -*- coding: utf-8 -*-
"""게임을 IGDB와 대조해 외부ID·원제·플랫폼·평점을 채운다.

두 경로로 찾는다.
  1) 스팀 appid가 있으면 그것으로 정확히 역조회 (틀릴 일이 없다)
  2) 없으면 제목으로 검색 — 영문 제목만 쓸 만하다.
     IGDB 한글 검색 히트율이 10%라 한글 제목은 건너뛰고 사람이 채운다.

결과는 igdb_match.json. 확인이 필요한 건은 igdb_확인필요.md로 뽑는다.

사용법:
    python match_igdb.py
"""
import io
import json
import os
import re
import sys
import time
import urllib.request

import config  # noqa: F401
from adapters import igdb
from config import API, headers

IDS_FILE = "db_ids.json"
OUT = "igdb_match.json"
TITLE_EN = "game_title_en.json"   # 한글 제목 → 영문 (IGDB 한글 검색이 안 되므로)
REVIEW = "igdb_확인필요.md"


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


def txt(prop):
    return "".join(x["plain_text"] for x in prop[prop["type"]])


def norm(s):
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def main():
    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)

    games = []
    for p in query_all(ids["work_db"]):
        kind = p["properties"]["종류"]["select"]
        if kind and kind["name"] == "게임":
            games.append({
                "page": p["id"],
                "제목": txt(p["properties"]["제목"]),
                "외부ID": txt(p["properties"]["외부ID"]),
                "출시일": (p["properties"]["출시·개봉일"]["date"] or {}).get("start"),
            })
    print(f"게임 {len(games)}건")

    # 1) 스팀 appid 경로
    appids = {}
    for g in games:
        m = re.match(r"steam:(\d+)", g["외부ID"] or "")
        if m:
            appids[m.group(1)] = g
    print(f"  스팀 appid 보유: {len(appids)}건")
    steam_to_igdb = igdb.by_steam_appids(list(appids))
    print(f"  IGDB 역조회 성공: {len(steam_to_igdb)}건")

    detail = igdb.games(set(steam_to_igdb.values()))
    out = {}
    for appid, gid in steam_to_igdb.items():
        d = detail.get(gid)
        if d:
            out[appids[appid]["제목"]] = dict(d, 경로="steam", 확신="확실")

    # 2) 제목 검색 경로 — 영문 제목, 그리고 대역표가 있는 한글 제목
    title_en = {}
    if os.path.exists(TITLE_EN):
        with io.open(TITLE_EN, encoding="utf-8") as f:
            title_en = {k: v for k, v in json.load(f).items()
                        if not k.startswith("_")}
    rest = [g for g in games
            if g["제목"] not in out
            and (not re.search("[가-힣]", g["제목"]) or g["제목"] in title_en)]
    print(f"  제목 검색 대상: {len(rest)}건 (대역표 {len(title_en)}건 보유)")
    low = []
    for i, g in enumerate(rest, 1):
        query = title_en.get(g["제목"], g["제목"])
        try:
            cands = igdb.search(query)
        except Exception as e:
            print(f"    [{g['제목']}] 실패: {e}", file=sys.stderr)
            continue
        if not cands:
            low.append((g["제목"], None))
            continue
        best = None
        for c in cands:
            if norm(c["이름"]) == norm(query):
                best = c
                break
        if best is None:
            best = cands[0]
            low.append((g["제목"], best["이름"]))
            conf = "낮음"
        else:
            conf = "확실"
        out[g["제목"]] = dict(best, 경로="검색", 확신=conf)
        if i % 20 == 0:
            print(f"    {i}/{len(rest)}")

    korean = [g["제목"] for g in games
              if g["제목"] not in out and re.search("[가-힣]", g["제목"])]

    with io.open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n매칭 {len(out)}건 / 한글 제목이라 미매칭 {len(korean)}건")

    lines = ["# IGDB 확인 필요", "",
             f"## 자동 매칭이 애매한 것 ({len(low)}건)", ""]
    for t, got in low:
        lines.append(f"- `{t}` → {got or '검색 결과 없음'}")
    lines += ["", f"## 한글 제목이라 자동 매칭 불가 ({len(korean)}건)", "",
              "IGDB는 한글 검색 히트율이 10%라 자동으로는 못 찾는다.", ""]
    for t in sorted(korean):
        lines.append(f"- {t}")
    with io.open(REVIEW, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"{REVIEW} 저장")


if __name__ == "__main__":
    main()
