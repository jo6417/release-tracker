# -*- coding: utf-8 -*-
"""스팀 어댑터 — 보유 라이브러리와 플레이 기록."""
import json
import os
import urllib.parse
import urllib.request

API = "https://api.steampowered.com"


def _get(path, **params):
    params["key"] = os.environ.get("STEAM_API_KEY", "")
    url = f"{API}{path}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read())


def steam_id(vanity):
    """프로필 주소명 → SteamID64"""
    d = _get("/ISteamUser/ResolveVanityURL/v1/", vanityurl=vanity)
    return d["response"].get("steamid")


def owned_games(steamid):
    """보유 게임 목록. 플레이시간(분)과 마지막 플레이 시각 포함."""
    d = _get("/IPlayerService/GetOwnedGames/v1/", steamid=steamid,
             include_appinfo=1, include_played_free_games=1)
    out = []
    for g in d.get("response", {}).get("games", []):
        out.append({
            "appid": g["appid"],
            "이름": g.get("name", ""),
            "플레이분": g.get("playtime_forever", 0),
            "최근플레이": g.get("rtime_last_played", 0),
        })
    return out
