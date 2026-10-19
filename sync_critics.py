# -*- coding: utf-8 -*-
"""게임의 메타크리틱·오픈크리틱 점수를 노션 작품 DB에 채운다.

`평점`은 IGDB 종합 점수(유저+평론 섞임)를 매칭 때 한 번 넣는 자리라,
사람들이 실제로 보는 두 숫자와 어긋난다. 그 둘을 따로 둔다.

    메타크리틱   metacritic.com 작품 페이지의 JSON-LD(aggregateRating)
                 → 안 맞으면 스팀 상점 API의 metacritic 값 (PC판 점수)
    오픈크리틱   위키데이터에서 IGDB 슬러그 → OpenCritic ID(P2864)를 찾고
                 opencritic.com 작품 페이지의 topCriticScore

둘 다 공식 API가 아니다. 메타크리틱은 API가 없고, 오픈크리틱 API는
RapidAPI 키가 필요하다. 그래서 공개 페이지에 박힌 값을 읽는다.
메타크리틱의 내부 검색 API는 사이트에 박힌 키를 빌려 써야 해서 쓰지 않는다.

**대상**: 종류=게임, 외부ID에 igdb:가 있고 이미 출시된 것.
**주기**: 점수는 출시 직후 몇 주 동안만 움직인다.
    - 처음 보는 게임 → 조회
    - 출시 180일 이내 → 7일마다 다시
    - 그 뒤 → 90일마다 다시
조회 기록은 critic_state.json에 남긴다(러너가 커밋한다). 매일 돌아도
위 조건에 걸린 것만 보므로 대부분의 날은 몇 건뿐이다.

노션 값은 다를 때만 쓴다. 두 속성은 track.py의 WATCH에 없어 알림이 가지 않는다.

사용법:
    python sync_critics.py --dry            # 미리보기
    python sync_critics.py --limit 80       # 이번 실행 상한
    python sync_critics.py --only "엘든 링" --force
"""
import argparse
import datetime
import html
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import config  # noqa: F401  (.env 로드)
from adapters import igdb
from config import API, headers

IDS_FILE = "db_ids.json"
STATE_FILE = "critic_state.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"}
WD_UA = {"User-Agent": "release-tracker/1.0 (https://github.com/jo6417/release-tracker)",
         "Accept": "application/sparql-results+json"}
RECENT_DAYS = 180      # 이 안이면 점수가 아직 움직인다
RECENT_EVERY = 7
OLD_EVERY = 90
GAP = 1.0              # 같은 사이트 연속 요청 간격(초)

P_MC = "메타크리틱"
P_OC = "오픈크리틱"


# ─────────────────────────────────────────────
# 노션
# ─────────────────────────────────────────────
def query_all(dbid):
    out, cursor = [], None
    while True:
        body = {"page_size": 100,
                "filter": {"property": "종류", "select": {"equals": "게임"}}}
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


def num(prop):
    return (prop or {}).get("number")


# ─────────────────────────────────────────────
# 바깥 소스
# ─────────────────────────────────────────────
_last = {}


def fetch(url, site, hdr=None):
    """같은 사이트는 GAP 간격을 지킨다. 실패하면 (상태코드, '')."""
    wait = GAP - (time.time() - _last.get(site, 0))
    if wait > 0:
        time.sleep(wait)
    _last[site] = time.time()
    try:
        req = urllib.request.Request(url, headers=hdr or UA)
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def igdb_info(ids):
    """IGDB id → 이름·슬러그·출시일"""
    out, ids = {}, list(ids)
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        rows = igdb.query("games", "fields name,slug,first_release_date; "
                                   f"where id = ({','.join(map(str, chunk))}); limit 500;")
        for g in rows:
            rel = g.get("first_release_date")
            out[g["id"]] = {
                "이름": g.get("name", ""),
                "슬러그": g.get("slug", ""),
                "출시일": (datetime.datetime.utcfromtimestamp(rel).date()
                        if rel else None),
            }
    return out


def wikidata_oc(slugs):
    """IGDB 슬러그 → OpenCritic ID. 위키데이터에 없으면 빠진다."""
    out, slugs = {}, [s for s in slugs if s]
    for i in range(0, len(slugs), 150):
        vals = " ".join('"%s"' % s.replace('"', "") for s in slugs[i:i + 150])
        q = ("SELECT ?igdb ?oc WHERE { VALUES ?igdb {%s} "
             "?item wdt:P5794 ?igdb . ?item wdt:P2864 ?oc }" % vals)
        url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": q})
        for attempt in range(4):
            st, body = fetch(url, "wikidata", WD_UA)
            if st != 429:
                break
            time.sleep(15 * (attempt + 1))   # 위키데이터는 몰아 물으면 잠시 막는다
        if st != 200:
            print(f"  [위키데이터] HTTP {st} — 이번엔 오픈크리틱 ID를 못 찾음", file=sys.stderr)
            continue
        for b in json.loads(body)["results"]["bindings"]:
            out.setdefault(b["igdb"]["value"], b["oc"]["value"])
    return out


def opencritic(oc_id):
    """평론가 상위 점수(반올림). 아직 리뷰가 모자라면 None."""
    st, t = fetch(f"https://opencritic.com/game/{oc_id}/x", "opencritic")
    if st != 200:
        return None
    m = re.search(r"topCriticScore&q;:(-?[\d.]+)", t)
    if not m:
        return None
    v = float(m.group(1))
    return round(v) if v >= 0 else None


def mc_slug(name):
    n = name.lower()
    for ch in ("'", "’", ".", "&"):
        n = n.replace(ch, "")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", n)).strip("-")


def metacritic_page(slug, year):
    """작품 페이지의 메타크리틱. 출시 연도가 ±1년을 벗어나면 다른 작품으로 본다
    (리메이크·동명 작품이 슬러그를 나눠 쓴다)."""
    st, t = fetch(f"https://www.metacritic.com/game/{slug}/", "metacritic")
    if st != 200:
        return None
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', t, re.S):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if d.get("@type") != "VideoGame":
            continue
        pub = (d.get("datePublished") or "")[:4]
        if year and pub.isdigit() and abs(int(pub) - year) > 1:
            return None
        v = (d.get("aggregateRating") or {}).get("ratingValue")
        return int(v) if isinstance(v, (int, float)) and v > 0 else None
    return None


def metacritic_steam(appid):
    url = ("https://store.steampowered.com/api/appdetails?"
           + urllib.parse.urlencode({"appids": appid, "filters": "metacritic"}))
    st, t = fetch(url, "steam")
    if st != 200:
        return None
    try:
        e = json.loads(t).get(str(appid)) or {}
    except ValueError:
        return None
    data = e.get("data") or {}
    return (data.get("metacritic") or {}).get("score") if e.get("success") else None


# ─────────────────────────────────────────────
def due(entry, released, today, force):
    if force or not entry or not entry.get("확인"):
        return True
    last = datetime.date.fromisoformat(entry["확인"])
    every = RECENT_EVERY if (today - released).days <= RECENT_DAYS else OLD_EVERY
    return (today - last).days >= every


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="노션에 쓰지 않음")
    ap.add_argument("--limit", type=int, default=80, help="이번 실행에서 조회할 최대 건수")
    ap.add_argument("--only", help="제목에 이 글자가 든 것만")
    ap.add_argument("--force", action="store_true", help="주기 무시하고 다시 조회")
    a = ap.parse_args()

    today = datetime.date.today()
    with io.open(IDS_FILE, encoding="utf-8") as f:
        dbid = json.load(f)["work_db"]
    state = {}
    if os.path.exists(STATE_FILE):
        with io.open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)

    rows = []
    for p in query_all(dbid):
        pr = p["properties"]
        title = txt(pr.get("제목"))
        if a.only and a.only not in title:
            continue
        ext = txt(pr.get("외부ID"))
        g = re.search(r"igdb:(\d+)", ext)
        if not g:
            continue
        s = re.search(r"steam:(\d+)", ext)
        rows.append({"pid": p["id"], "제목": title, "igdb": int(g.group(1)),
                     "steam": s.group(1) if s else None,
                     P_MC: num(pr.get(P_MC)), P_OC: num(pr.get(P_OC))})

    info = igdb_info({r["igdb"] for r in rows})
    todo = []
    for r in rows:
        i = info.get(r["igdb"])
        if not i or not i["출시일"] or i["출시일"] > today:
            continue                      # 아직 안 나왔다 — 점수가 있을 수 없다
        r.update(i)
        if due(state.get(str(r["igdb"])), i["출시일"], today, a.force):
            todo.append(r)
    # 한 번도 안 본 것 → 최근 출시 순
    todo.sort(key=lambda r: (str(r["igdb"]) in state, -r["출시일"].toordinal()))
    print(f"게임 {len(rows)}건 중 출시됨·조회 차례 {len(todo)}건 (이번 상한 {a.limit})")
    todo = todo[:a.limit]

    need = [r["슬러그"] for r in todo if not (state.get(str(r["igdb"])) or {}).get("oc")]
    oc_ids = wikidata_oc(need) if need else {}

    stat = {"메타크리틱": 0, "스팀": 0, "오픈크리틱": 0, "기록": 0}
    for r in todo:
        key = str(r["igdb"])
        ent = state.get(key) or {}
        oc_id = ent.get("oc") or oc_ids.get(r["슬러그"])

        mc, src = None, None
        for slug in dict.fromkeys(filter(None, [ent.get("mc"), r["슬러그"], mc_slug(r["이름"])])):
            mc = metacritic_page(slug, r["출시일"].year)
            if mc:
                ent["mc"], src = slug, "메타크리틱"
                break
        if not mc and r["steam"]:
            mc = metacritic_steam(r["steam"])
            src = "스팀" if mc else None
        oc = opencritic(oc_id) if oc_id else None

        if src:
            stat[src] += 1
        if oc:
            stat["오픈크리틱"] += 1
        ent.update({"oc": oc_id, "확인": today.isoformat(), P_MC: mc, P_OC: oc})
        state[key] = ent

        props = {}
        # 이번에 못 읽었다고 있던 값을 지우지 않는다 — 사이트가 잠깐 막혔을 수 있다
        if mc is not None and mc != r[P_MC]:
            props[P_MC] = {"number": mc}
        if oc is not None and oc != r[P_OC]:
            props[P_OC] = {"number": oc}
        mark = "*" if props else " "
        print(f" {mark} {r['제목'][:22]:22} 메타크리틱 {mc if mc is not None else '-':>3}"
              f"{"(스팀)" if src == "스팀" else "":5} 오픈크리틱 {oc if oc is not None else '-':>3}")
        if props and not a.dry:
            patch_page(r["pid"], props)
            stat["기록"] += 1
            time.sleep(0.34)

    if not a.dry:
        with io.open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)

    print(f"\n조회 {len(todo)}건 · 메타크리틱 {stat['메타크리틱']} (+스팀 {stat['스팀']}) · "
          f"오픈크리틱 {stat['오픈크리틱']} · 노션 갱신 {stat['기록']}건"
          f"{' (미리보기라 쓰지 않음)' if a.dry else ''}")


if __name__ == "__main__":
    main()
