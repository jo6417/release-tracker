# -*- coding: utf-8 -*-
"""하드웨어(스위치2 젤다 에디션·아미보 등)와 메이저 게임쇼의 새 뉴스를 잡는다.

하드웨어는 IGDB·TMDB 어디에도 없어서 발매일·예약 개시를 확인할 자동
수단이 없다(작품 DB의 다른 게임·영화와 다른 점). 대신 뉴스는 "예약판매
시작"·"출시일 연기" 같은 사건을 직접 말해준다.

**날짜를 기사 본문에서 파싱하지 않는다.** "9월 25일" 같은 문자열을 정확히
뽑는 건 스크립트 혼자 자주 틀린다. 대신 새 기사 자체를 신호로 보고
사람에게 넘긴다 — `refresh_dates.py`가 출시일 변경을 감지만 하고 반영은
사람이 하는 것과 같은 원칙이다.

대상 둘:
  1. 하드웨어 행 (진행도(게임)가 구매 대기·예약주문인 것만 — 이미 받았거나
     안 살 것은 검색할 이유가 없다)
  2. 메이저 게임쇼 4개 (Summer Game Fest·gamescom·TGS·TGA) — 사용자 지정.
     **일정 등록·변경에 쓸 기사만** 알린다(2026-09-15 사용자 요청 — 행사
     이름만 들어간 기사가 매일 떠서 소음이었다). 일정 DB를 보고 모드를 고른다:
       - 다가오는 확정 일정이 없음 → 제목에 행사명 + 일정 발표 단어가 있는 기사
       - 다가오는 확정 일정이 있음 → 제목에 행사명 + 연기·변경·취소 단어가 있는 기사

같은 기사를 두 번 알리지 않는다 — 채널당 본 기사 id를 상태 파일에 남긴다
(`sync_media.py`의 방식과 같다).

사용법:
    python sync_hardware_news.py --dry
    python sync_hardware_news.py
"""
import argparse
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
import notify
from config import API, headers

IDS_FILE = "db_ids.json"
STATE_FILE = "hardware_news_state.json"
NEWS_API = "https://naverapihub.apigw.ntruss.com/search/v1/news"
MAJOR_SHOWS = ["Summer Game Fest", "gamescom", "Tokyo Game Show", "The Game Awards"]
# 기사 제목·일정 DB 이름에서 행사를 알아볼 별칭. 소문자 비교, 공백 무시.
SHOW_ALIASES = {
    "Summer Game Fest": ["summergamefest", "서머게임페스트", "썸머게임페스트", "sgf"],
    "gamescom": ["gamescom", "게임스컴", "게임즈컴"],
    "Tokyo Game Show": ["tokyogameshow", "도쿄게임쇼", "tgs"],
    "The Game Awards": ["thegameawards", "더게임어워드", "게임어워드", "tga"],
}
# 일정이 아직 없을 때: 개최 일정이 나왔다는 기사
ANNOUNCE_WORDS = ["일정", "개최", "날짜", "열린다", "연다", "개막", "확정", "dates", "date"]
# 일정이 이미 있을 때: 그 일정이 바뀌었다는 기사
CHANGE_WORDS = ["연기", "변경", "취소", "연장", "축소", "단축", "온라인", "무산", "중단",
                "postpone", "delay", "cancel"]
SEEN_MAX = 30  # 검색어당 기억해둘 기사 id 상한 (state 파일이 무한정 안 커지게)


def _naver_key():
    kid = os.environ.get("NAVER_API_KEY_ID")
    k = os.environ.get("NAVER_API_KEY")
    if not kid or not k:
        raise RuntimeError("NAVER_API_KEY_ID / NAVER_API_KEY가 없습니다 (.env 확인)")
    return kid, k


def search_news(query, display=10):
    kid, k = _naver_key()
    q = urllib.parse.urlencode({"query": query, "display": display, "sort": "date"})
    req = urllib.request.Request(f"{NEWS_API}?{q}",
                                 headers={"X-NCP-APIGW-API-KEY-ID": kid,
                                          "X-NCP-APIGW-API-KEY": k})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read()).get("items", [])


def strip_tags(s):
    return s.replace("<b>", "").replace("</b>", "").replace("&quot;", '"').replace("&amp;", "&")


def query_all(dbid, flt=None):
    out, cursor = [], None
    while True:
        body = {"page_size": 100}
        if flt:
            body["filter"] = flt
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
    return "".join(x["plain_text"] for x in prop.get(prop["type"], []))


WATCH_STAGES = {"구매 대기", "예약주문"}


def search_query(title):
    """검색어에서 괄호 안 부연설명을 뗀다. "아미보 미넬 골렘 (티어스 오브 더
    킹덤)" 같은 정식 명칭 그대로는 뉴스에 잘 안 걸린다 — 기사는 그렇게
    안 쓴다."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", title).strip() or title


def hardware_targets(work_db):
    """검색할 하드웨어 제목만 추린다 — 이미 받았거나 안 살 건 뺀다."""
    rows = query_all(work_db, {"property": "종류", "select": {"equals": "하드웨어"}})
    out = []
    for p in rows:
        pr = p["properties"]
        stage = pr.get("진행도(게임)", {}).get("select")
        stage = stage["name"] if stage else None
        if stage in WATCH_STAGES:
            out.append(txt(pr["제목"]))
    return out


def _norm(s):
    return re.sub(r"\s+", "", s).lower()


def mentions_show(show, text):
    t = _norm(text)
    for a in SHOW_ALIASES[show]:
        if len(a) <= 3:  # tga·tgs·sgf는 영단어 속에 섞이지 않게 경계를 본다
            if re.search(rf"(?<![a-z]){a}(?![a-z])", re.sub(r"\s+", " ", text.lower())):
                return True
        elif a in t:
            return True
    return False


def confirmed_shows(schedule_db, today):
    """일정 DB에서 오늘 이후(진행 중 포함) 확정 일정이 잡힌 게임쇼 → 날짜 문자열."""
    rows = query_all(schedule_db, {"and": [
        {"property": "날짜정밀도", "select": {"equals": "확정"}},
        {"or": [{"property": "종류", "select": {"equals": "행사"}},
                {"property": "종류", "select": {"equals": "발표"}}]},
    ]})
    out = {}
    for p in rows:
        pr = p["properties"]
        d = (pr.get("날짜") or {}).get("date")
        if not d or not d.get("start"):
            continue
        last = (d.get("end") or d["start"])[:10]
        if last < today:
            continue
        name = txt(pr["이름"])
        for show in MAJOR_SHOWS:
            if show not in out and mentions_show(show, name):
                out[show] = d["start"][:10] + (f"~{d['end'][:10]}" if d.get("end") else "")
    return out


def relevant(show, title, has_schedule):
    """게임쇼 기사가 일정 등록·변경에 쓸 만한가. 제목만 본다."""
    if not mentions_show(show, title):
        return False
    words = CHANGE_WORDS if has_schedule else ANNOUNCE_WORDS
    low = title.lower()
    return any(w in low for w in words)


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with io.open(STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with io.open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)

    # (표시용 이름, 검색어, 종류). 하드웨어는 괄호를 뗀 검색어를 쓰되
    # 상태 파일 key·알림 표시는 원래 제목 그대로 쓴다 — 사람이 봤을 때
    # 어떤 물건인지 바로 읽혀야 한다.
    queries = [(t, search_query(t), "하드웨어") for t in hardware_targets(ids["work_db"])]
    queries += [(s, s, "게임쇼") for s in MAJOR_SHOWS]
    today = time.strftime("%Y-%m-%d")
    try:
        booked = confirmed_shows(ids["schedule_db"], today)
    except urllib.error.HTTPError as e:
        # 일정 DB를 못 읽으면 '일정 없음'으로 보고 발표 기사만 거른다
        print(f"일정 DB 조회 실패 {e.code} — 발표 기사 기준으로 거름", file=sys.stderr)
        booked = {}
    for s_ in MAJOR_SHOWS:
        print(f"  [게임쇼] {s_}: " + (f"일정 있음({booked[s_]}) → 변경 기사만" if s_ in booked
                                     else "일정 없음 → 일정 발표 기사만"))
    print(f"검색 대상 {len(queries)}건 (하드웨어 {sum(1 for _,_,k in queries if k=='하드웨어')} "
          f"/ 게임쇼 {sum(1 for _,_,k in queries if k=='게임쇼')})")

    state = load_state()
    alerts = []
    for name, query, kind in queries:
        try:
            items = search_news(query)
        except urllib.error.HTTPError as e:
            print(f"[{query}] 검색 실패 {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
            continue
        seen = set(state.get(name, {}).get("본것", []))
        fresh = [it for it in items if it["link"] not in seen]
        if kind == "게임쇼":
            fresh = [it for it in fresh
                     if relevant(name, strip_tags(it["title"]), name in booked)]
        ids_now = [it["link"] for it in items]
        first = name not in state
        if first:
            print(f"  [{kind}] {name}: 기준값만 잡음 ({len(items)}건)")
        elif fresh:
            print(f"  [{kind}] {name}: 새 기사 {len(fresh)}건")
            alerts.append((name, kind, fresh[:3]))
        state[name] = {"본것": (list(seen | set(ids_now)))[-SEEN_MAX:],
                       "확인": time.strftime("%Y-%m-%d")}
        time.sleep(0.2)

    if not a.dry:
        save_state(state)

    if alerts and not a.dry:
        summary = ["[뉴스] " + ", ".join(n for n, _, _ in alerts)]
        details = []
        for name, kind, arts in alerts:
            lines = [strip_tags(it["title"]) + f"  {it['originallink'] or it['link']}"
                     for it in arts]
            if kind == "게임쇼" and name in booked:
                lines.append(f"→ 등록된 일정({booked[name]})이 바뀌었으면 채팅으로 반영해달라고 말씀해주세요")
            elif kind == "게임쇼":
                lines.append("→ 일정 DB에 없음. 개최 일정이면 채팅으로 등록해달라고 말씀해주세요")
            else:
                lines.append("→ 관련 있으면 채팅으로 반영해달라고 말씀해주세요")
            details.append((f"[{kind}] {name}", lines))
        notify.send_card(f"관련 뉴스 {len(alerts)}건", summary=summary,
                         details=details, kinds=["뉴스"], count=len(alerts))
        if not notify.spooling():
            print("알림 카드 1장 발송 완료")
    elif not alerts:
        print("새 기사 없음")

    if a.dry:
        print("--dry 모드: 상태 미저장")


if __name__ == "__main__":
    main()
