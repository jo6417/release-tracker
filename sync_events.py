# -*- coding: utf-8 -*-
"""게임쇼(닌텐도 다이렉트·State of Play·gamescom 등)를 IGDB에서 읽어
일정 DB에 기록하고, 우리 관심작이 나왔으면 작품과 연결한다.

목적은 하나다. **게임쇼에서 관심작이 공개돼도 지금은 아무도 모른다.**
2026-09-09 닌텐도 다이렉트에서 86건이 공개됐고 그중 6건이 작품 DB에 있는
관심작이었는데, 그 사실을 알 방법이 없었다. 게임쇼는 놓친 작품이 한꺼번에
쏟아지는 자리라 HANDOFF 1순위("놓치고 넘어간 작품 발견")와 정확히 같다.

IGDB `/events`가 각 쇼에서 공개된 게임을 IGDB game id 배열로 준다. 작품 DB의
`외부ID`(igdb:숫자)와 대조하면 **제목 매칭 없이 정확히** 관심작을 골라낼 수
있다 (제목 매칭이 개명에 약하다는 건 이미 다른 곳에서 확인된 문제다).

IGDB는 사후 등록형이라 미래 이벤트는 안 준다 — `where start_time > now`가
항상 0건이다. 그래서 이 스크립트는 **지난 N일**만 본다. 미래 예고는
사용자가 채팅으로 넣거나(연례 대형 행사) 별도 경로(불시 쇼케이스, 미착수)로
채운다. 자세한 설계는 `설계-게임쇼.md` 참조.

작은 쇼(참가 게임 10~20건)까지 전부 일정 행으로 만들면 캘린더가 뒤덮인다.
그래서 **관심작이 하나라도 있거나, 공개 게임 수가 MIN_GAMES 이상인 것만**
일정으로 남긴다. 나머지는 조용히 넘어간다 — 놓쳐도 큰 쇼는 언젠가 다시
걸린다.

노션에 반영은 하되(일정 DB는 부속 정보라 사람 값을 덮을 일이 없다),
**작품 DB는 절대 건드리지 않는다.** 관심작 연결은 relation 추가일 뿐이다.

사용법:
    python sync_events.py --dry
    python sync_events.py
"""
import argparse
import datetime
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request

import config  # noqa: F401  (.env 로드)
from adapters import igdb
from config import API, headers

IDS_FILE = "db_ids.json"
LOOKBACK_DAYS = 10
# 이보다 참가 게임이 적은 쇼는 관심작이 없으면 건너뛴다. 숫자는 튜닝 대상 —
# 실측(2026-09) 기준 닌텐도 다이렉트 86 / State of Play 34 / Gamescom ONL 73 /
# 소규모 인디쇼 10~20건. 20으로 두면 소규모는 걸러지고 대형 쇼는 다 걸린다.
MIN_GAMES = 20
# 기대작 칸에 나열할 후보 제목 상한. 다이렉트 한 번에 80건씩 나오는데
# 다 적으면 칸이 넘친다 — 몇 건까지 자동 등록 후보로 올릴지는 확인 큐를
# 만들 때(설계-확인큐.md) 다시 정한다. 지금은 참고용 텍스트일 뿐이다.
MAX_기대작 = 6
KST = datetime.timezone(datetime.timedelta(hours=9))


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


def create_page(dbid, props):
    req = urllib.request.Request(
        f"{API}/pages",
        data=json.dumps({"parent": {"database_id": dbid},
                         "properties": props}).encode(),
        headers=headers(), method="POST")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:400]
            # 400(validation_error)은 재시도해도 안 바뀌는 진짜 버그다.
            # 일시적인 것만 재시도한다.
            if e.code in (429, 500, 502, 503) and attempt < 2:
                print(f"[재시도 {attempt+1}] {e.code}: {body}", file=sys.stderr)
                time.sleep(3)
                continue
            print(f"[에러] {e.code}: {body}", file=sys.stderr)
            raise


def txt(prop):
    return "".join(x["plain_text"] for x in prop.get(prop["type"], []))


def work_igdb_map(work_db):
    """작품 DB의 igdb 외부ID -> page id. 제목 매칭은 쓰지 않는다."""
    out = {}
    for p in query_all(work_db):
        kind = p["properties"]["종류"]["select"]
        if not kind or kind["name"] != "게임":
            continue
        m = re.search(r"igdb:(\d+)", txt(p["properties"]["외부ID"]))
        if m:
            out[int(m.group(1))] = p["id"]
    return out


def known_event_ids(schedule_db):
    """이미 일정 DB에 있는 igdb_event: 외부ID 집합. 중복 방지용."""
    out = set()
    for p in query_all(schedule_db):
        m = re.search(r"igdb_event:(\d+)", txt(p["properties"]["외부ID"]))
        if m:
            out.add(int(m.group(1)))
    return out


def fetch_events(since_ts):
    return igdb.query(
        "events",
        f"fields id, name, start_time, end_time, live_stream_url, games; "
        f"where start_time > {since_ts} & start_time < {int(time.time())}; "
        f"sort start_time desc; limit 60;")


def fetch_game_names(ids):
    """게임 id -> 이름. 한 번에 최대 500개까지 묶어 보낸다(IGDB 상한)."""
    out = {}
    ids = sorted(ids)
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        where = ",".join(str(x) for x in chunk)
        for g in igdb.query("games", f"fields id,name; where id=({where}); limit 500;"):
            out[g["id"]] = g["name"]
    return out


def kst_iso(unix_ts):
    return datetime.datetime.fromtimestamp(unix_ts, tz=KST).isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="노션에 쓰지 않고 결과만 출력")
    a = ap.parse_args()

    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)

    igdb_map = work_igdb_map(ids["work_db"])
    print(f"작품 DB igdb 보유 {len(igdb_map)}건")

    since = int(time.time()) - LOOKBACK_DAYS * 86400
    events = fetch_events(since)
    print(f"지난 {LOOKBACK_DAYS}일 게임쇼 {len(events)}건 조회됨")

    known = known_event_ids(ids["schedule_db"])

    targets = []
    for e in events:
        if e["id"] in known:
            continue
        game_ids = e.get("games") or []
        matched = [gid for gid in game_ids if gid in igdb_map]
        if not matched and len(game_ids) < MIN_GAMES:
            continue
        targets.append((e, matched))

    if not targets:
        print("새로 기록할 게임쇼가 없습니다")
        return

    # 후보 제목은 한 번에 묶어 조회한다 (이벤트마다 따로 부르면 API 호출이 는다)
    unmatched_ids = set()
    for e, matched in targets:
        unmatched_ids |= set(e.get("games") or []) - set(matched)
    names = fetch_game_names(unmatched_ids) if unmatched_ids else {}

    n_created = n_linked = 0
    for e, matched in targets:
        when = kst_iso(e["start_time"])
        props = {
            "이름": {"title": [{"type": "text", "text": {"content": e["name"]}}]},
            "종류": {"select": {"name": "발표"}},
            "날짜": {"date": {"start": when}},
            "날짜정밀도": {"select": {"name": "확정"}},
            "캘린더노출": {"checkbox": True},
            "외부ID": {"rich_text": [{"type": "text",
                                    "text": {"content": f"igdb_event:{e['id']}"}}]},
        }
        if e.get("live_stream_url"):
            props["스트림"] = {"url": e["live_stream_url"]}
        if matched:
            props["작품"] = {"relation": [{"id": igdb_map[gid]} for gid in matched]}
            n_linked += len(matched)

        cand_ids = [gid for gid in (e.get("games") or []) if gid not in matched]
        if cand_ids:
            cand_names = [names[g] for g in cand_ids if g in names][:MAX_기대작]
            extra = len(cand_ids) - len(cand_names)
            text = ", ".join(cand_names)
            if extra > 0:
                text += f" 외 {extra}건"
            props["기대작"] = {"rich_text": [{"type": "text", "text": {"content": text[:1900]}}]}

        local = datetime.datetime.fromisoformat(when)
        print(f"  {e['name'][:44]:44} {local.strftime('%Y-%m-%d %H:%M')} "
              f"공개 {len(e.get('games') or []):>3}건 · 관심작 {len(matched)}건")

        if not a.dry:
            create_page(ids["schedule_db"], props)
            time.sleep(0.34)
        n_created += 1

    print(f"\n{'기록 예정' if a.dry else '기록'} {n_created}건 "
          f"(관심작 연결 {n_linked}건)")
    if a.dry:
        print("--dry 모드: 노션 미변경")


if __name__ == "__main__":
    main()
