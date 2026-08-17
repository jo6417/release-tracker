# -*- coding: utf-8 -*-
"""IGDB 어댑터 — 게임 메타데이터.

Twitch OAuth로 토큰을 받아 쓴다. 토큰은 약 60일 유효하지만
매 실행마다 새로 받는다 (호출 1회라 부담 없음).
"""
import json
import os
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
    out = []
    for g in rows:
        out.append({
            "id": g["id"],
            "이름": g.get("name", ""),
            "출시일": (time.strftime("%Y-%m-%d", time.localtime(g["first_release_date"]))
                    if g.get("first_release_date") else None),
            "플랫폼": [p.get("abbreviation") for p in g.get("platforms", [])
                    if p.get("abbreviation")],
            "시리즈": [f["name"] for f in g.get("franchises", [])],
            "평점": round(g["total_rating"], 1) if g.get("total_rating") else None,
            "url": g.get("url"),
        })
    return out
