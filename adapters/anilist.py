# -*- coding: utf-8 -*-
"""AniList 어댑터 — 애니메이션 회차별 방영일.

TMDB는 미래 회차 날짜가 비어 있을 때가 있다. 그러면 완결 예정일을
'남은 화수 × 7일'로 어림잡을 수밖에 없는데, AniList는 방영표를
회차 단위로 갖고 있어서 그 자리를 메워준다.

나무위키의 '회차 목록'과 같은 정보다. 나무위키 쪽은 본문이 자바스크립트로
그려져서 HTML만 받아서는 표가 안 나오고(확인함: /raw, /w 둘 다 껍데기만 온다),
약관상으로도 긁을 자리가 아니다. AniList는 공개 GraphQL API이고 키도 필요 없다.

키 불필요. 초당 제한이 있어 호출 사이를 벌린다.
"""
import json
import time
import urllib.error
import urllib.request

API = "https://graphql.anilist.co"
UA = "release-tracker/1.0 (personal use)"
_last_call = [0.0]

_QUERY = """
query ($s: String) {
  Media(search: $s, type: ANIME) {
    id
    title { romaji native english }
    episodes
    status
    startDate { year month day }
    endDate { year month day }
    airingSchedule { nodes { episode airingAt } }
  }
}
"""


def _post(query, variables):
    wait = 0.7 - (time.time() - _last_call[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(
        API, data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": UA}, method="POST")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                _last_call[0] = time.time()
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt * 2)
                continue
            raise
    raise RuntimeError("AniList 재시도 초과")


def _ymd(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


def schedule(title):
    """제목으로 찾아 회차별 방영일을 돌려준다.

    반환: {"제목":, "총화수":, "상태":, "방영표": {회차: "YYYY-MM-DD"}} 또는 None
    """
    if not title:
        return None
    try:
        d = _post(_QUERY, {"s": title})
    except Exception:
        return None
    m = (d.get("data") or {}).get("Media")
    if not m:
        return None
    nodes = ((m.get("airingSchedule") or {}).get("nodes")) or []
    return {
        "제목": (m.get("title") or {}).get("native") or (m.get("title") or {}).get("romaji"),
        "총화수": m.get("episodes"),
        "상태": m.get("status"),
        "방영표": {n["episode"]: _ymd(n["airingAt"]) for n in nodes if n.get("airingAt")},
    }


def finale(title, total=None):
    """마지막 화 방영일. 방영표가 끝까지 있을 때만 돌려준다."""
    s = schedule(title)
    if not s or not s["방영표"]:
        return None
    n = total or s["총화수"]
    if not n:
        return None
    return s["방영표"].get(n)
