# -*- coding: utf-8 -*-
"""작품 한 줄 소개를 외부 소스에서 긁어 노션 `소개` 속성에 채운다.

알림에 제목만 뜨면 "이게 뭐였지"로 끝나서 그냥 넘기게 된다. 특히 추천 칸은
몇 년 전에 적어둔 작품이 올라오는 자리라 제목이 아무 말도 안 해준다.
한 줄이라도 붙어 있으면 그 자리에서 "볼까 말까"를 정할 수 있다.

소스 우선순위는 **한국어가 있는 쪽 먼저**다:

    게임      스팀 short_description (한국어)  →  IGDB summary (영문)
    영화·시리즈 TMDB overview (ko-KR)          →  TMDB overview (영문)

한 번 채우면 다시 건드리지 않는다(`--force` 예외). 매일 실행에 넣어두고
`--limit`으로 조금씩 밀면 새로 들어온 작품이 알아서 채워진다.

사용법:
    python sync_intro.py --dry          # 미리보기
    python sync_intro.py --limit 80     # 빈 것부터 80건
    python sync_intro.py --only 종규 --force
"""
import argparse
import html
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import config  # noqa: F401  (.env 로드)
from adapters import igdb, tmdb
from config import API, headers

IDS_FILE = "db_ids.json"
MAX_LEN = 400          # 노션에 넣을 상한. 알림에서 자르는 건 describe.intro_line
STEAM_APP = "https://store.steampowered.com/api/appdetails"


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


def txt(prop):
    if not prop:
        return ""
    return "".join(x["plain_text"] for x in prop.get(prop["type"], []))


def clean(text):
    """줄바꿈·HTML 태그·반복 공백을 걷어낸 한 문단."""
    if not text:
        return ""
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = text.replace(chr(10), " ").replace(chr(13), " ")
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text[:MAX_LEN]


def steam_intro(appid):
    """스팀 상점 소개문. 한국어 페이지가 있으면 한국어로 온다."""
    url = f"{STEAM_APP}?" + urllib.parse.urlencode(
        {"appids": appid, "l": "koreana", "cc": "kr"})
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            d = json.loads(r.read())
    except Exception:
        return None
    entry = d.get(str(appid)) or {}
    if not entry.get("success"):
        return None
    return clean((entry.get("data") or {}).get("short_description"))


def tmdb_intro(media_type, tid):
    """TMDB 줄거리. ko-KR이 비어 있는 작품이 꽤 있어 영문으로 한 번 더 본다."""
    for lang in ("ko-KR", "en-US"):
        try:
            d = tmdb._get(f"/{media_type}/{tid}", language=lang)
        except Exception:
            return None
        got = clean(d.get("overview"))
        if got:
            return got
    return None


def igdb_intro(gid_map):
    """IGDB summary — 영문뿐이지만 스팀에 없는 게임의 유일한 소개문이다."""
    out = {}
    ids = list(gid_map)
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        try:
            rows = igdb.query("games",
                              "fields id,summary; "
                              f"where id = ({','.join(map(str, chunk))}); limit 500;")
        except Exception as e:
            print(f"[IGDB 실패] {e}", file=sys.stderr)
            continue
        for g in rows:
            got = clean(g.get("summary"))
            if got:
                out[g["id"]] = got
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="노션에 쓰지 않고 결과만")
    ap.add_argument("--force", action="store_true", help="이미 채워진 것도 덮어씀")
    ap.add_argument("--only", help="제목에 이 문자열이 든 작품만")
    ap.add_argument("--limit", type=int, default=120,
                    help="한 번에 처리할 최대 건수 (매일 조금씩 밀기 위한 것)")
    a = ap.parse_args()

    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)

    todo = []
    for p in query_all(ids["work_db"]):
        pr = p["properties"]
        title = txt(pr.get("제목"))
        if a.only and a.only not in title:
            continue
        if txt(pr.get("소개")) and not a.force:
            continue
        todo.append((p["id"], title,
                     (pr.get("종류", {}).get("select") or {}).get("name"),
                     txt(pr.get("외부ID"))))
    print(f"소개 없는 작품 {len(todo)}건 (이번 실행 상한 {a.limit})")
    todo = todo[:a.limit]

    # IGDB는 100건씩 묶어 물어볼 수 있다. 스팀·TMDB가 못 채운 것만 남겨 한 번에.
    need_igdb, results, stat = {}, {}, {"스팀": 0, "TMDB": 0, "IGDB": 0, "없음": []}

    for pid, title, kind, ext in todo:
        got = None
        if kind == "게임":
            m = re.search(r"steam:(\d+)", ext)
            if m:
                got = steam_intro(m.group(1))
                if got:
                    stat["스팀"] += 1
        else:
            m = re.search(r"tmdb:(movie|tv):(\d+)", ext)
            if m:
                got = tmdb_intro(m.group(1), m.group(2))
                if got:
                    stat["TMDB"] += 1
        if not got:
            g = re.search(r"igdb:(\d+)", ext)
            if g:
                need_igdb.setdefault(int(g.group(1)), []).append(pid)
            else:
                stat["없음"].append(title)
                continue
        else:
            results[pid] = got

    if need_igdb:
        for gid, text in igdb_intro(need_igdb).items():
            for pid in need_igdb[gid]:
                results[pid] = text
                stat["IGDB"] += 1
        for gid, pids in need_igdb.items():
            for pid in pids:
                if pid not in results:
                    stat["없음"].append(pid)

    name_of = {pid: title for pid, title, _, _ in todo}
    for pid, text in results.items():
        print(f"  {name_of.get(pid, pid)[:24]:24} {text[:70]}")
        if not a.dry:
            patch_page(pid, {"소개": {"rich_text": [
                {"type": "text", "text": {"content": text[:1900]}}]}})
            time.sleep(0.34)

    print(f"\n{'채울 예정' if a.dry else '채움'} {len(results)}건 "
          f"(스팀 {stat['스팀']} / TMDB {stat['TMDB']} / IGDB {stat['IGDB']}) "
          f"· 소스 없음 {len(stat['없음'])}건")


if __name__ == "__main__":
    main()
