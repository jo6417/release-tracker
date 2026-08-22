# -*- coding: utf-8 -*-
"""TMDB 어댑터 — 영화/시리즈 조회.

이 파일 하나만 고치면 TMDB 관련 동작이 전부 바뀐다.
다른 소스가 깨져도 여기는 영향받지 않는다.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.themoviedb.org/3"
ANIMATION_GENRE = 16          # TMDB 장르 ID: Animation
_last_call = [0.0]


def _get(path, **params):
    params["api_key"] = os.environ.get("TMDB_API_KEY", "")
    url = f"{API}{path}?" + urllib.parse.urlencode(params)
    # 초당 호출 제한 여유 있게
    wait = 0.06 - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                _last_call[0] = time.time()
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError("TMDB 재시도 초과")


def search(title, year=None):
    """영화·시리즈를 한 번에 검색해 후보 목록을 돌려준다."""
    res = _get("/search/multi", query=title, language="ko-KR",
               include_adult="false")
    out = []
    for r in res.get("results", []):
        if r.get("media_type") not in ("movie", "tv"):
            continue
        is_tv = r["media_type"] == "tv"
        date = r.get("first_air_date" if is_tv else "release_date") or ""
        out.append({
            "id": r["id"],
            "media_type": r["media_type"],
            "제목": r.get("name" if is_tv else "title", ""),
            "원제": r.get("original_name" if is_tv else "original_title", ""),
            "날짜": date,
            "연도": date[:4],
            "애니메이션": ANIMATION_GENRE in (r.get("genre_ids") or []),
            "인기도": r.get("popularity", 0),
        })
    return out


def kr_release(tmdb_id, media_type):
    """국내 개봉일. 없으면 None."""
    try:
        if media_type == "movie":
            d = _get(f"/movie/{tmdb_id}/release_dates")
            for entry in d.get("results", []):
                if entry.get("iso_3166_1") == "KR":
                    dates = [x["release_date"][:10] for x in entry.get("release_dates", [])
                             if x.get("release_date")]
                    return min(dates) if dates else None
        else:
            d = _get(f"/tv/{tmdb_id}", language="ko-KR")
            return d.get("first_air_date") or None
    except urllib.error.HTTPError:
        return None
    return None


def _segmentations(word, max_parts=4):
    """붙여 쓴 한글을 2~3글자 위주로 끊어본 후보들.

    TMDB 한글 검색이 띄어쓰기를 요구해서, '맨오브스틸' 같은 제목을
    '맨 오브 스틸'로 복원하기 위한 것.
    """
    n = len(word)
    if n < 2 or n > 14:
        return []
    out = []

    def walk(pos, parts):
        if len(parts) > max_parts:
            return
        if pos == n:
            if len(parts) > 1:
                out.append(parts[:])
            return
        for size in (2, 3, 4, 5, 1):
            if pos + size <= n:
                parts.append(word[pos:pos + size])
                walk(pos + size, parts)
                parts.pop()

    walk(0, [])
    # 2~3글자 조각이 많은 순으로
    # 조각 수가 적고 2~3글자 위주인 것을 먼저
    out.sort(key=lambda ps: (len(ps),
                             -sum(1 for p in ps if len(p) in (2, 3)),
                             sum(1 for p in ps if len(p) == 1)))
    return [" ".join(ps) for ps in out]


def _variants(title):
    """검색해볼 질의어들을 순서대로. 붙여쓴 한글·부제 표기를 흡수한다."""
    out, seen = [], set()

    def add(q):
        q = (q or "").strip(" :·-,")
        if q and q not in seen:
            seen.add(q)
            out.append(q)

    add(title)
    add(re.sub("[ ]{2,}", " ", re.sub("[:：·_—–-]", " ", title)))
    head = re.split("[:：]", title)[0].strip()
    add(head)
    for base in (head, title):
        base = re.sub("[0-9]+$", "", base).strip()
        if base and " " not in base:
            for cand in _segmentations(base)[:12]:
                add(cand)
    kor = re.sub("[^가-힣]", "", head)
    for n in (4, 3):
        if len(kor) > n:
            add(kor[:n])
    return out


def search_smart(title, year=None, max_queries=12):
    """여러 형태로 질의해 후보를 모두 모은다.

    첫 성공에서 멈추면 '미션임파서블'처럼 엉뚱한 편이 걸려도 그대로 굳어버려서,
    후보를 모아 두고 호출 쪽에서 연도로 고르게 한다.

    반환: (후보목록, 실제로 쓴 질의어들)
    """
    want = re.sub("[^0-9a-z가-힣]", "", title.lower())
    pool, used = {}, []
    for q in _variants(title)[:max_queries]:
        try:
            res = search(q)
        except Exception:
            continue
        if not res:
            continue
        used.append(q)
        for r in res:
            pool.setdefault((r["id"], r["media_type"]), r)
        # 제목이 정확히 같고 연도까지 맞으면 더 볼 필요 없음
        for r in res:
            got = re.sub("[^0-9a-z가-힣]", "", r["제목"].lower())
            if got == want and year and r["연도"] == year:
                return list(pool.values()), used
    return list(pool.values()), used


# 아직 안 나온 작품은 날짜 칸이 '2026-01-01'처럼 연도만 뜻하는 자리표시로
# 채워져 있는 일이 흔하다. 그걸 확정으로 읽으면 잘못된 알림이 나간다.
_PLANNED = {"Planned", "Rumored"}


def details(tmdb_id, media_type):
    """개봉·공개일과 그 정밀도. 못 찾으면 None.

    반환: {"날짜", "정밀도", "국내날짜", "상태"}
    """
    try:
        if media_type == "movie":
            d = _get(f"/movie/{tmdb_id}", language="ko-KR")
            date = d.get("release_date") or ""
        else:
            d = _get(f"/tv/{tmdb_id}", language="ko-KR")
            date = d.get("first_air_date") or ""
    except urllib.error.HTTPError:
        return None

    status = d.get("status") or ""
    if not date:
        precision = "미정"
    elif status in _PLANNED and date[5:] == "01-01":
        precision = "연도"
    else:
        precision = "확정"

    return {
        "날짜": date or None,
        "정밀도": precision,
        "국내날짜": kr_release(tmdb_id, media_type) if media_type == "movie" else None,
        "상태": status,
    }
