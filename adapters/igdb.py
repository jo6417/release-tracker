# -*- coding: utf-8 -*-
"""IGDB 어댑터 — 게임 메타데이터.

Twitch OAuth로 토큰을 받아 쓴다. 토큰은 약 60일 유효하지만
매 실행마다 새로 받는다 (호출 1회라 부담 없음).
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.igdb.com/v4"
STEAM_SOURCE = 1          # external_game_source: 1 = Steam
_token = [None]
_last = [0.0]


def _auth():
    if _token[0]:
        return _token[0]
    body = urllib.parse.urlencode({
        "client_id": os.environ.get("TWITCH_CLIENT_ID", ""),
        "client_secret": os.environ.get("TWITCH_CLIENT_SECRET", ""),
        "grant_type": "client_credentials"}).encode()
    req = urllib.request.Request("https://id.twitch.tv/oauth2/token",
                                 data=body, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        _token[0] = json.loads(r.read())["access_token"]
    return _token[0]


def query(endpoint, q):
    """IGDB는 초당 4회 제한. 간격을 지킨다."""
    wait = 0.26 - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(
        f"{API}/{endpoint}", data=q.encode("utf-8"),
        headers={"Client-ID": os.environ.get("TWITCH_CLIENT_ID", ""),
                 "Authorization": f"Bearer {_auth()}"},
        method="POST")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                _last[0] = time.time()
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503):
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError("IGDB 재시도 초과")


def by_steam_appids(appids):
    """스팀 appid 목록 → {appid: igdb_game_id}"""
    out = {}
    appids = [str(a) for a in appids]
    for i in range(0, len(appids), 100):
        chunk = appids[i:i + 100]
        uids = ",".join(f'"{a}"' for a in chunk)
        rows = query("external_games",
                     f"fields game,uid; "
                     f"where external_game_source = {STEAM_SOURCE} & uid = ({uids}); "
                     f"limit 500;")
        for r in rows:
            if r.get("game"):
                out[r["uid"]] = r["game"]
    return out


def games(ids):
    """IGDB 게임 id 목록 → 상세 정보"""
    out = {}
    ids = list(ids)
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        rows = query("games",
                     "fields name,first_release_date,url,platforms.abbreviation,"
                     "franchises.name,total_rating,category,parent_game; "
                     f"where id = ({','.join(map(str, chunk))}); limit 500;")
        for g in rows:
            out[g["id"]] = {
                "id": g["id"],
                "이름": g.get("name", ""),
                "출시일": (time.strftime("%Y-%m-%d",
                                      time.localtime(g["first_release_date"]))
                        if g.get("first_release_date") else None),
                "플랫폼": [p.get("abbreviation") for p in g.get("platforms", [])
                        if p.get("abbreviation")],
                "시리즈": [f["name"] for f in g.get("franchises", [])],
                "평점": round(g["total_rating"], 1) if g.get("total_rating") else None,
                "url": g.get("url"),
            }
    return out


def search(name, limit=5):
    rows = query("games", f'search "{name}"; '
                          f"fields name,first_release_date,url,platforms.abbreviation,"
                          f"franchises.name,total_rating; limit {limit};")
    return [_row(g) for g in rows]


def _row(g):
    return {
        "id": g["id"],
        "이름": g.get("name", ""),
        "출시일": (time.strftime("%Y-%m-%d", time.localtime(g["first_release_date"]))
                if g.get("first_release_date") else None),
        "플랫폼": [p.get("abbreviation") for p in g.get("platforms", [])
                if p.get("abbreviation")],
        "시리즈": [f["name"] for f in g.get("franchises", [])],
        "평점": round(g["total_rating"], 1) if g.get("total_rating") else None,
        "url": g.get("url"),
    }


def by_name(name, limit=5):
    """이름이 정확히 같은 게임을 찾는다.

    `search`는 관련도 순이라 흔한 단어가 제목이면 정작 그 게임이 안 나온다 -
    "Control"을 검색하면 Star Control이, "원신"의 대역인 Genshin Impact를
    검색하면 그 DLC가 위로 올라온다. 이름을 안다면 검색이 아니라 조회를
    해야 한다. 대소문자는 무시한다(~ 연산자).
    """
    esc = name.replace('"', '')
    rows = query("games", f'fields name,first_release_date,url,platforms.abbreviation,'
                          f'franchises.name,total_rating; '
                          f'where name ~ "{esc}"; limit {limit};')
    return [_row(g) for g in rows]


def steam_appids(igdb_ids):
    """IGDB 게임 id → 스팀 appid. 미보유 게임의 가격을 조회하기 위한 것."""
    out = {}
    ids = list(igdb_ids)
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        rows = query("external_games",
                     "fields game,uid; "
                     f"where external_game_source = {STEAM_SOURCE} "
                     f"& game = ({','.join(map(str, chunk))}); limit 500;")
        for r in rows:
            if r.get("game") and r.get("uid", "").isdigit():
                out[r["game"]] = r["uid"]
    return out


# release_dates.category → 노션 '날짜정밀도'
# 0=YYYY-MM-DD, 1=YYYY-MM, 2=YYYY, 3~6=분기, 7=미정
DATE_CATEGORY = {0: "확정", 1: "월", 2: "연도",
                 3: "분기", 4: "분기", 5: "분기", 6: "분기", 7: "미정"}

_HUMAN = [
    (re.compile(r"^[A-Za-z]{3} \d{1,2}, \d{4}$"), "확정"),
    (re.compile(r"^[A-Za-z]{3} \d{4}$"), "월"),
    (re.compile(r"^Q[1-4] \d{4}$", re.I), "분기"),
    (re.compile(r"^\d{4}$"), "연도"),
]


def _precision(row):
    """category를 우선 보고, 없으면 human 문자열로 판단한다.

    first_release_date만 보면 '2026년 언젠가'도 2026-12-31 같은 날짜로
    내려와서 확정과 구분이 되지 않는다. 그 구분이 알림의 핵심이라
    두 경로를 다 둔다.
    """
    cat = row.get("category")
    if cat is None:
        cat = row.get("date_format")     # 필드명이 바뀐 API 대비
    if isinstance(cat, int) and cat in DATE_CATEGORY:
        return DATE_CATEGORY[cat]
    human = (row.get("human") or "").strip()
    for pattern, level in _HUMAN:
        if pattern.match(human):
            return level
    return "미정"


# release_dates.status — 1 알파, 2 베타, 3 얼리액세스, 4 오프라인, 5 취소,
# 6 정식 출시(1.0), 34 선행 플레이(예약구매·특정 에디션), 7 목록 삭제.
# 값이 없는 줄도 많은데, 그건 대개 평범한 정식 출시다.
_CANCELLED = 5
_FULL = 6


def _pick(rows):
    """한 게임의 출시일 줄들 중에서 쓸 것을 고른다.

    지역·플랫폼마다 줄이 따로 있고, 그중에서는 가장 이른 것을 쓴다.

    다만 얼리액세스·베타·선행 플레이는 "같은 것의 다른 지역판"이 아니라 아예
    다른 사건이다. 그냥 가장 이른 것을 고르면 얼리액세스 시작일을 정식
    출시일로 착각한다 -- Moonlighter 2는 얼리액세스가 2025-11-19, 정식이
    2026-09-02인데 전자를 골라서 "287일 앞당겨졌다"고 잘못 알렸다(2026-08-30).
    요즘 인디 게임은 대부분 이 경로라 그냥 두면 계속 재발한다.

    그래서 정식 출시 줄이 하나라도 있으면 그중에서만 고른다. 아직 얼리액세스만
    나온 게임은 비교할 정식 줄이 없으므로 예전처럼 가장 이른 것을 쓴다 --
    그 편이 "미정"으로 비우는 것보다 쓸모 있다.
    """
    rows = [r for r in rows if r.get("status") != _CANCELLED]
    full = [r for r in rows if r.get("status") in (_FULL, None)]
    use = full or rows
    return min(use, key=lambda r: r["date"]) if use else None


def releases(ids):
    """IGDB 게임 id → {"날짜": "YYYY-MM-DD", "정밀도": ...}"""
    out, by_game = {}, {}
    ids = list(ids)
    fields = "fields game,date,category,date_format,human,status;"
    for i in range(0, len(ids), 25):
        chunk = ids[i:i + 25]
        q = (f"{fields} where game = ({','.join(map(str, chunk))}) "
             f"& date != null; sort date asc; limit 500;")
        try:
            rows = query("release_dates", q)
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            # 필드 하나가 없는 API 버전 — 최소한으로 다시 묻는다.
            # status가 빠지면 _pick은 예전처럼 가장 이른 것을 고른다.
            fields = "fields game,date,human;"
            q = (f"{fields} where game = ({','.join(map(str, chunk))}) "
                 f"& date != null; sort date asc; limit 500;")
            rows = query("release_dates", q)
        for r in rows:
            if r.get("game") and r.get("date"):
                by_game.setdefault(r["game"], []).append(r)

    for gid, rows in by_game.items():
        best = _pick(rows)
        if best:
            out[gid] = {"날짜": time.strftime("%Y-%m-%d", time.gmtime(best["date"])),
                        "정밀도": _precision(best)}
    return out
