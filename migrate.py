# -*- coding: utf-8 -*-
"""
네이버 캘린더 .ics → Notion 작품 DB / 일정 DB 이관

1) .ics 파싱 + 스티커 기반 분류
2) 같은 작품의 여러 행을 하나로 묶기 (Anthem 데모/얼리액세스/정식 등)
3) Notion에 작품 1행 + 일정 N행 생성

사용법:
    NOTION_TOKEN=ntn_xxx python3 migrate.py --dry     # 미리보기 (Notion 안 건드림)
    NOTION_TOKEN=ntn_xxx python3 migrate.py           # 실제 이관
"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict

from config import API, CALENDAR_VISIBLE, NOTION_TOKEN, headers

ICS = os.environ.get(
    "ICS_PATH",
    os.path.join(os.path.expanduser("~"), "Downloads",
                 "Calendar_ejrrms22_2026-08-04-232847.ics"),
)
IDS_FILE = "db_ids.json"
STATE_FILE = "migrate_state.json"   # 중단 후 재개용 진행 상태
EXCLUDE_FILE = "review_exclude.json"   # apply_review.py가 만든 제외 목록
ENRICHED_FILE = "migrate_enriched.json"   # apply_tmdb.py가 만든 보강본

# ── 스티커 → 종류 ───────────────────────────────
# 노란별(574) / 589는 2015년 초까지만 게임 표시로 썼고, 이후에는
# 잡다한 개인 표시(월급날, 결혼, 블랙프라이데이 등)로 용도가 바뀌었다.
STAR_STICKERS = {"574", "589"}
STAR_GAME_UNTIL = "2015-06-01"

STICKER_KIND = {
    "565": "게임", "574": "게임", "589": "게임",
    "504": "영화", "564": "시리즈",
    # 563(도서)은 SCHEMA.md 기준 이관 제외 — 작품 DB 종류에 '도서'가 없음
}
# 이관 제외 스티커 (개인 일정 등)
SKIP_STICKERS = {
    "563", "570", "501", "591", "516", "534", "519", "983", "638", "506",
    "672", "719", "677", "545", "1229", "764", "530", "674", "560",
    "508", "001", "525",
}
# 스티커를 잘못 찍은 것 (원본 제목 → 실제 종류)
FORCE_KIND = {
    "위쳐 시즌3 2부": "영화",   # 게임 아이콘으로 찍혔지만 넷플릭스 드라마
}

# 스티커가 개인이어도 이관할 예외 (판정 완료분)
FORCE_INCLUDE = {
    "메탈기어솔리드5:팬텀페인": "게임",
    "고스트리콘 오픈베타": "게임",
    "뉴던전스트라이커 OBT": "게임",
    "바이오쇼크 인피니트": "게임",
    "폴아웃4": "게임",
    "동키콩 바난자": "게임",
    "지아이조2 개봉": "영화",
    "스타트렉 다크니스": "영화",
}
# 스티커가 출시여도 제외할 예외
FORCE_EXCLUDE = {
    "v4.0 업데이트", "윈도우 MR 출시", "마영전 업데이트",
    # 게임 내 이벤트 / 운영 / 행사 — 출시가 아님
    "마영전 골든타임", "BIC 2022",
    "검은사막 PC방 시작",
    "FILAx우왁굳 시즌2 출시일", "스팀 겨울할인~01.05",
    # 하드웨어 / 서비스 자체 (작품이 아님)
    "스위치2 발매", "PS5 예약발송(게임앤라이프)", "디즈니플러스",
    "듀얼센스 아스트로봇 에디션",   # 컨트롤러(하드웨어)
    "신비한 동물사전 블루레이", "어쌔신 크리드 블루레이",   # 소장품 구매 기록
    "윈도우10 크리에이터 업데이트", "CEMU 업데이트",
    # 개인 일정인데 게임 스티커가 붙은 것
    "기숙사 룸메신청",
    # 서비스/플랫폼 런칭 자체 (작품이 아님)
    "구글 스태디아 출시",
    # 테스트 신청 접수 — 출시가 아님 (A:IR = 블루홀 에어, 2017-11-13 CBT 신청 시작)
    "AIR CBT신청",
}

# 원본 제목의 오타 / 표기 흔들림 교정
TITLE_FIX = {
    "Sword o the sea": "Sword of the Sea",
    "사힐런트힐f": "SILENT HILL f",
    "The outer world": "The Outer Worlds",
    "라오어2 예판": "라스트오브어스2 예판",
    "시크릿 인베이전 마지막화": "시크릿 인베이젼 마지막화",
    "뎅케르크": "덩케르크",
}

# 제목에 섞인 소유/구독 표기 → 소유처 속성으로
OWNER_TAGS = [
    ("gamepass|game[ ]*pass|게임패스|xgp", "게임패스"),
]
# 제목 끝에 붙은 사건 표현 — 작품명이 아니므로 떼어낸다.
# ('드라큘라:전설의 시작'처럼 제목의 일부인 낱말은 건드리지 않는다)
TITLE_SUFFIX = "[ ]+(개봉|첫방|첫방송|발매|정발|출시일)[ ]*$"
# 순차 공개물의 조각 표기 — 본편 작품으로 합치기 위해 뗀다
PART_SUFFIX = "[ ]*([0-9]+부|마지막화|최종화|완결)[ ]*$"

# (원본 제목, 날짜) — 같은 작품을 두 번 적은 중복 기록. 늦게 적힌 쪽을 버린다.
DROP_EVENTS = {
    ("인투 더 스톰", "2014-08-28"),      # 2014-08-08과 동일 영화
    ("어쌔신크리드 - 발할라", "2020-11-12"),  # 11-10과 동일
    ("뮬란", "2020-03-07"),              # 코로나로 연기 → 2020-09-10이 실제
}

# 제목에 섞인 플랫폼 표기 → 플랫폼 속성으로 옮긴다
PLATFORM_TAGS = [
    ("(?<![A-Za-z0-9])PS5(?![0-9])", "PS5"),
    ("(?<![A-Za-z0-9])PS4(?![0-9])", "PS4"),
    ("(?<![A-Za-z0-9])PS3(?![0-9])", "PS3"),
    ("[(]xbox[)]|(?<![A-Za-z])xbox(?![A-Za-z])|엑박", "Xbox"),
    ("스위치2", "Switch2"),
    ("(?<![A-Za-z])SW(?![A-Za-z])|(?<![A-Za-z])NS(?![A-Za-z])|스위치", "Switch"),
    ("for[ ]*steam|(?<![A-Za-z])PC(?![A-Za-z])", "PC"),
]
# 제목에 섞인 OTT 표기 → 공개처 속성으로
OTT_TAGS = [
    (r"넷플릭스|netflix", "넷플릭스"),
    ("디즈니[ ]*플러스|디즈니[+]|(?<![A-Za-z가-힣])디플(?![A-Za-z가-힣])",
     "디즈니+"),
    (r"왓챠", "왓챠"), (r"티빙", "티빙"), (r"웨이브", "웨이브"),
    (r"애플\s*TV\+?", "애플TV+"), (r"쿠팡플레이", "쿠팡플레이"),
]

# ── 제목 → 일정 종류 ────────────────────────────
SCHEDULE_PATTERNS = [
    (r"테스터\s*발표|당첨자\s*발표", "베타테스트"),
    (r"오픈\s*베타|OBT\b", "베타테스트"),
    (r"클로즈드?\s*베타|CBT\b", "베타테스트"),
    (r"알파\s*테스트", "알파테스트"),
    (r"\bPS4\s*베타|\b베타\b", "베타테스트"),
    (r"DEMO\b|데모", "데모"),
    (r"얼리\s*[엑액]세스", "얼리액세스"),
    (r"예판|예약\s*구매|예약\s*발송", "예약구매"),
    (r"\bDLC\b", "DLC"),
    (r"무료\s*배포", "무료배포"),
    (r"(\d+)\s*부\b", "파트공개"),
    (r"시즌\s*(\d+)", "시즌시작"),
    (r"최종화|마지막화|완결", "최종화"),
]

def parse_ics(path):
    raw = io.open(path, "rb").read().decode("utf-8", errors="replace")
    out = []
    for b in raw.split("BEGIN:VEVENT")[1:]:
        m = re.search(r"X-NAVER-STICKER(?:;[^:]*)?:(\d+)", b)
        s = re.search(r"SUMMARY:(.*)", b)
        d = re.search(r"DTSTART[^:]*:(\d{8})", b)
        if not (m and s and d):
            continue
        title = s.group(1).strip().replace("\\,", ",").replace("\\;", ";")
        title = TITLE_FIX.get(title, title)
        out.append({
            "sid": m.group(1),
            "title": title,
            "date": f"{d.group(1)[:4]}-{d.group(1)[4:6]}-{d.group(1)[6:]}",
        })
    return out


def classify(rec):
    """이관 대상이면 종류를 반환, 아니면 None"""
    t = rec["title"]
    if t in FORCE_EXCLUDE:
        return None
    if t in FORCE_KIND:
        return FORCE_KIND[t]
    if t in FORCE_INCLUDE:
        return FORCE_INCLUDE[t]
    if rec["sid"] in SKIP_STICKERS:
        return None
    if rec["sid"] in STAR_STICKERS and rec["date"] >= STAR_GAME_UNTIL:
        return None
    return STICKER_KIND.get(rec["sid"])


def schedule_kind(title, work_kind):
    for pat, kind in SCHEDULE_PATTERNS:
        if re.search(pat, title, re.I):
            return kind
    if work_kind == "만화":
        return "회차"
    if work_kind in ("영화",):
        return "극장개봉"
    if work_kind == "시리즈":
        return "OTT공개"
    return "정식출시"


def base_title(title, work_kind):
    """부속 일정 표기를 떼어낸 작품명"""
    t = title
    # 괄호/플랫폼/부속 표기 제거
    t = re.sub(r"\((?:PS[45]|PC|SW|Switch|스팀|게임패스|한글|공식한글)[^)]*\)", "", t, flags=re.I)
    t = re.sub(r"[:：]\s*(?:DLC|End of|잃어버린|황금나무|발할라|오버추어|거인의|레벨레이션)[^\n]*", "", t, flags=re.I)
    t = re.sub(r"\bDLC\b[^\n]*", "", t, flags=re.I)
    t = re.sub(r"테스터\s*발표|당첨자\s*발표", "", t)
    t = re.sub(r"(?:오픈|클로즈드?)?\s*(?:베타|알파)\s*(?:테스트)?|OBT|CBT|DEMO|데모|VIP|OPEN", "", t, flags=re.I)
    t = re.sub(r"얼리\s*[엑액]세스|예판|예약\s*구매|예약\s*발송", "", t)
    t = re.sub(r"\s*[-–]\s*$", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" :·-,")
    return t or title


def extract_tags(title):
    """제목에서 플랫폼·OTT·소유 표기와 사건 표현을 떼어낸다.

    반환: (정리된 제목, 플랫폼, 공개처, 소유처)
    """
    t = title
    plats, otts, owners = [], [], []
    for pat, name in PLATFORM_TAGS:
        if re.search(pat, t, re.I):
            if name not in plats:
                plats.append(name)
            t = re.sub(pat, " ", t, flags=re.I)
    for pat, name in OTT_TAGS:
        if re.search(pat, t, re.I):
            if name not in otts:
                otts.append(name)
            t = re.sub(pat, " ", t, flags=re.I)
    for pat, name in OWNER_TAGS:
        if re.search(pat, t, re.I):
            if name not in owners:
                owners.append(name)
            t = re.sub(pat, " ", t, flags=re.I)
    edition = "한글판|공식한글|한글|한국어|디지털|예정"
    t = re.sub("[(]([^)]*)[)]",
               lambda m: "(" + re.sub(edition, " ", m.group(1)) + ")", t)
    t = re.sub("[ ]*(" + edition + ")[ ]*$", "", t)
    t = re.sub("[][()]", " ", t)
    t = re.sub("[ ]{2,}", " ", t).strip(" :·-,")
    t = re.sub(TITLE_SUFFIX, "", t).strip(" :·-,")
    t = re.sub(PART_SUFFIX, "", t).strip(" :·-,")
    return (t or title), plats, otts, owners


def norm_key(s):
    """작품 묶기용 정규화 키"""
    s = s.lower()
    s = re.sub(r"[\s:：\-–_,.!?'\"()\[\]]", "", s)
    return s


def build(records):
    works = {}          # key -> work dict
    for r in records:
        kind = classify(r)
        if not kind:
            continue
        if (r["title"], r["date"]) in DROP_EVENTS:
            continue
        cleaned, plats, otts, owners = extract_tags(r["title"])
        bt = base_title(cleaned, kind)
        key = (kind, norm_key(bt))   # 종류가 다르면 별개 작품
        if key not in works:
            works[key] = {"제목": bt, "종류": kind, "스티커": r["sid"],
                          "플랫폼": [], "공개처": [], "소유처": [], "일정": []}
        for v in plats:
            if v not in works[key]["플랫폼"]:
                works[key]["플랫폼"].append(v)
        for v in otts:
            if v not in works[key]["공개처"]:
                works[key]["공개처"].append(v)
        for v in owners:
            if v not in works[key]["소유처"]:
                works[key]["소유처"].append(v)
        sk = schedule_kind(r["title"], kind)
        works[key]["일정"].append({
            "이름": r["title"],
            "종류": sk,
            "날짜": r["date"],
            "회차": None,
        })
    # 검토에서 제외 표시한 것 걷어내기
    if os.path.exists(EXCLUDE_FILE):
        with io.open(EXCLUDE_FILE, encoding="utf-8") as f:
            ex = json.load(f)
        drop_works = set(ex.get("works", []))
        drop_scheds = {(t, d) for t, d in ex.get("schedules", [])}
        for key in [k for k, v in works.items() if v["제목"] in drop_works]:
            del works[key]
        for v in works.values():
            v["일정"] = [s for s in v["일정"]
                       if (v["제목"], s["날짜"]) not in drop_scheds]
        if drop_works or drop_scheds:
            print(f"검토 반영: 작품 {len(drop_works)}개 / 일정 {len(drop_scheds)}건 제외")

    # 대표출시일 = 정식출시/극장개봉 등 메인 사건. 그 행은 작품에 흡수하고
    # 베타·데모·DLC·회차 같은 부속 사건만 일정 행으로 남긴다.
    for w in works.values():
        w["일정"].sort(key=lambda x: x["날짜"])
        main = next((s for s in w["일정"]
                     if s["종류"] in CALENDAR_VISIBLE or s["종류"] == "파트공개"), None)
        # 메인 사건이 없으면(베타만 기록된 작품 등) 대표출시일은 비워둔다.
        # 베타 날짜를 대표출시일에 넣으면 나중에 API 날짜와 비교할 때 오탐이 된다.
        w["출시·개봉일"] = main["날짜"] if main else None
        w["메인종류"] = main["종류"] if main else None
        w["종료일"] = None
        if main is not None:
            w["일정"] = [s for s in w["일정"] if s is not main]
            # 순차 공개 드라마: 최종화/마지막 파트를 방영 종료일로 흡수한다.
            # (쉬헐크 2022-08-17 ~ 2022-10-13 처럼 기간으로 보이게)
            tail = [s for s in w["일정"]
                    if s["종류"] in ("최종화", "파트공개") and s["날짜"] > main["날짜"]]
            if tail:
                w["종료일"] = max(s["날짜"] for s in tail)
                w["일정"] = [s for s in w["일정"] if s not in tail]
    return list(works.values())


# ── Notion 입력 ────────────────────────────────
def post(path, payload):
    req = urllib.request.Request(f"{API}{path}", data=json.dumps(payload).encode(),
                                 headers=headers(), method="POST")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt)
                continue
            print(f"[에러] {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
            raise
    raise RuntimeError("재시도 초과")


def txt(v):
    return [{"type": "text", "text": {"content": str(v)[:1900]}}] if v else []


def load_state():
    if os.path.exists(STATE_FILE):
        with io.open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state):
    with io.open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def push(works, ids):
    """중단되어도 migrate_state.json 기준으로 이어서 진행한다 (중복 생성 방지)."""
    wdb, sdb = ids["work_db"], ids["schedule_db"]
    today = time.strftime("%Y-%m-%d")
    state = load_state()
    if state:
        print(f"  이전 진행분 {len(state)}개 작품 발견 — 이어서 진행합니다.")

    for i, w in enumerate(works, 1):
        skey = f"{w['종류']}|{w['제목']}"
        st = state.get(skey)
        if st and st["sched"] >= len(w["일정"]):
            continue
        ref = w["출시·개봉일"] or (w["일정"][0]["날짜"] if w["일정"] else today)
        past = ref < today
        props = {
            "제목": {"title": txt(w["제목"])},
            "종류": {"select": {"name": w["종류"]}},
            "단계": {"select": {"name": "미확인" if past else "기대작"}},
            "스티커원본": {"rich_text": txt(w["스티커"])},
            "변경이력": {"rich_text": txt(f"{today}: 캘린더 이관")},
        }
        if w["플랫폼"]:
            props["플랫폼"] = {"multi_select": [{"name": v} for v in w["플랫폼"]]}
        if w["공개처"]:
            props["공개처"] = {"multi_select": [{"name": v} for v in w["공개처"]]}
        if w.get("애니메이션"):
            props["애니메이션"] = {"checkbox": True}
        if w.get("원제"):
            props["원제"] = {"rich_text": txt(w["원제"])}
        if w.get("외부ID"):
            props["외부ID"] = {"rich_text": txt(w["외부ID"])}
        if w.get("레퍼런스"):
            props["레퍼런스"] = {"url": w["레퍼런스"]}
        if w["소유처"]:
            props["소유처"] = {"multi_select": [{"name": v} for v in w["소유처"]]}
            if "게임패스" in w["소유처"]:
                props["게임패스"] = {"checkbox": True}
                props["획득경로"] = {"multi_select": [{"name": "구독포함"}]}
        if w["출시·개봉일"]:
            d = {"start": w["출시·개봉일"]}
            if w.get("종료일"):
                d["end"] = w["종료일"]
            props["출시·개봉일"] = {"date": d}
            props["날짜정밀도"] = {"select": {"name": "확정"}}
        else:
            props["날짜정밀도"] = {"select": {"name": "미정"}}
        if st is None:
            page = post("/pages", {"parent": {"database_id": wdb},
                                   "properties": props})
            st = state[skey] = {"page": page["id"], "sched": 0}
            save_state(state)
            time.sleep(0.34)

        for si, s in enumerate(w["일정"]):
            if si < st["sched"]:
                continue
            sp = {
                "이름": {"title": txt(s["이름"])},
                "종류": {"select": {"name": s["종류"]}},
                "날짜": {"date": {"start": s["날짜"]}},
                "날짜정밀도": {"select": {"name": "확정"}},
                "캘린더노출": {"checkbox": s["종류"] in CALENDAR_VISIBLE},
                "작품": {"relation": [{"id": st["page"]}]},
            }
            if s["회차"]:
                sp["회차"] = {"number": s["회차"]}
            post("/pages", {"parent": {"database_id": sdb}, "properties": sp})
            st["sched"] = si + 1
            save_state(state)
            time.sleep(0.34)   # Notion 3req/s 제한
        if i % 25 == 0:
            print(f"  {i}/{len(works)} 작품 완료")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="Notion에 쓰지 않고 결과만 출력")
    ap.add_argument("--ics", default=ICS, help="네이버 캘린더 .ics 경로")
    ap.add_argument("--limit", type=int, default=0,
                    help="앞의 N개 작품만 입력 (소량 테스트용)")
    ap.add_argument("--only", default="",
                    help="쉼표로 구분한 제목만 입력 (예: --only 타이탄폴,Anthem)")
    a = ap.parse_args()

    if not os.path.exists(a.ics):
        print(f".ics 파일을 찾을 수 없습니다: {a.ics}", file=sys.stderr)
        sys.exit(1)

    recs = parse_ics(a.ics)
    works = build(recs)
    # TMDB 반영본이 있으면 그것을 쓴다 (apply_tmdb.py가 만든다)
    if not a.dry and os.path.exists(ENRICHED_FILE):
        with io.open(ENRICHED_FILE, encoding="utf-8") as f:
            works = json.load(f)
        print(f"{ENRICHED_FILE} 사용 — TMDB 반영본")
    total_sched = sum(len(w["일정"]) for w in works)

    from collections import Counter
    print(f"이벤트 {len(recs)}건 → 작품 {len(works)}개 / 부속 일정 {total_sched}건")
    print("종류:", dict(Counter(w["종류"] for w in works)))
    print("작품 출시·개봉일 종류:",
          dict(Counter(w["메인종류"] or "없음(미정)" for w in works)))
    print("부속 일정 종류:", dict(Counter(s["종류"] for w in works for s in w["일정"])))

    multi = [w for w in works if w["일정"]]
    print(f"\n부속 일정이 있는 작품 {len(multi)}개 (상위 12):")
    for w in sorted(multi, key=lambda x: -len(x["일정"]))[:12]:
        kinds = ", ".join(f"{s['종류']}({s['날짜'][:7]})" for s in w["일정"][:5])
        head = (f"{w['메인종류']}({w['출시·개봉일']})" if w["메인종류"]
                else "없음(미정)")
        print(f"  {w['제목']} | 대표 {head} + 부속 {len(w['일정'])}: {kinds}")

    if a.dry:
        with io.open("migrate_preview.json", "w", encoding="utf-8") as f:
            json.dump(works, f, ensure_ascii=False, indent=1)
        print("\n--dry 모드: migrate_preview.json 저장. Notion 미변경.")
        return

    if not NOTION_TOKEN:
        print("NOTION_TOKEN이 없습니다.", file=sys.stderr)
        sys.exit(1)
    if not os.path.exists(IDS_FILE):
        print(f"{IDS_FILE}이 없습니다. create_db.py를 먼저 실행하세요.",
              file=sys.stderr)
        sys.exit(1)
    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)
    if a.only:
        want = [t.strip() for t in a.only.split(",") if t.strip()]
        works = [w for w in works if w["제목"] in want]
        missing = set(want) - {w["제목"] for w in works}
        if missing:
            print(f"찾지 못한 제목: {sorted(missing)}", file=sys.stderr)
        print(f"--only: {len(works)}개 작품만 입력합니다.")
    if a.limit:
        works = works[:a.limit]
        print(f"--limit {a.limit}: 앞의 {len(works)}개 작품만 입력합니다.")
    print("\nNotion 입력 시작...")
    push(works, ids)
    print(f"완료 (진행 상태: {STATE_FILE})")


if __name__ == "__main__":
    main()
