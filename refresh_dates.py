# -*- coding: utf-8 -*-
"""외부 소스에서 출시일을 다시 물어와 노션에 반영한다.

`track.py`는 '노션이 어제와 달라졌는가'만 본다. 그래서 이 단계가 없으면
바깥에서 날짜가 확정돼도 노션은 그대로고, `[날짜확정]` 알림은 사람이 직접
노션을 고쳤을 때만 울린다. 매일 실행에서 `track.py` **앞에** 두어야
같은 날 알림으로 이어진다.

`sync_series.py`가 이미 도는 작품은 방영 시작 이후를 본다(완결 추적).
이 스크립트는 그 앞 구간 — **아직 안 나온 것의 출시일** — 을 맡는다.

건드리는 범위를 좁게 잡았다.

- 날짜정밀도가 `확정`·`완결대기`인 행은 아예 보지 않는다. 사람이 정한 값을
  소스가 덮어쓰지 않게. 대가로 **출시 연기는 감지하지 못한다**
- 정밀도가 **올라갈 때만** 쓴다 (미정 → 연도 → 분기 → 월 → 확정)
- `월`·`분기`·`연도`면 출시·개봉일에 **그 구간의 첫날**을 넣는다.
  `track.py`의 `released()`가 쓰는 규칙과 같다 — 확정이 아닌 날짜는
  자리표시자이고, 대기를 푸는 근거로 쓰이지 않는다
- 영화는 국내 개봉일을 알게 되면 그걸로 덮어쓴다 (스키마가 국내/해외를
  나누지 않기로 한 것을 따른다)
- 바뀌는 값이 없으면 노션을 건드리지 않는다. 수정시각이 바뀌면 .ics가
  매번 새로 만들어져 캘린더가 무의미하게 재배포된다

사용법:
    python refresh_dates.py --dry      # 바꾸지 않고 무엇이 바뀔지만 출력
    python refresh_dates.py
"""
import argparse
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request

from adapters import igdb, tmdb
from config import API, headers

IDS_FILE = "db_ids.json"

# 낮을수록 거칠다. 올라갈 때만 쓴다.
RANK = {"미정": 0, "연도": 1, "분기": 2, "월": 3, "확정": 4}
# 이 값이 들어 있으면 손대지 않는다. `완결대기`는 사람이 직접 고르는 상태다.
KEEP = ("확정", "완결대기")


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


def patch(pid, props):
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
    return "".join(x["plain_text"] for x in prop[prop["type"]])


def placeholder(date, precision):
    """확정이 아닌 정밀도에서 출시·개봉일에 넣을 자리표시 날짜 (구간의 첫날)."""
    if not date:
        return None
    year, month = date[:4], int(date[5:7])
    if precision == "확정":
        return date
    if precision == "월":
        return f"{year}-{month:02d}-01"
    if precision == "분기":
        return f"{year}-{(month - 1) // 3 * 3 + 1:02d}-01"
    if precision == "연도":
        return f"{year}-01-01"
    return None


def read_rows(pages):
    """조회 대상만 추린다.

    반환: (대상 목록, 건너뛴 이유별 집계)
    """
    rows, skip = [], {"확정·완결대기": 0, "외부ID없음": 0, "종류없음": 0}
    for p in pages:
        props = p["properties"]
        kind = props["종류"]["select"]
        if not kind:
            skip["종류없음"] += 1
            continue
        precision = props["날짜정밀도"]["select"]
        precision = precision["name"] if precision else "미정"
        if precision in KEEP or precision not in RANK:
            skip["확정·완결대기"] += 1
            continue

        ext = txt(props["외부ID"])
        row = {"id": p["id"], "제목": txt(props["제목"]), "종류": kind["name"],
               "정밀도": precision, "props": props}

        if kind["name"] == "게임":
            m = re.search(r"igdb:(\d+)", ext)
            if not m:
                skip["외부ID없음"] += 1
                continue
            row["igdb"] = int(m.group(1))
        else:
            m = re.search(r"tmdb:(movie|tv):(\d+)", ext)
            if not m:
                skip["외부ID없음"] += 1
                continue
            row["tmdb"] = (m.group(1), int(m.group(2)))
        rows.append(row)
    return rows, skip


def plan(row, found):
    """노션에 쓸 변경분. 바꿀 게 없으면 빈 dict.

    정밀도가 올라갔거나, 같은 정밀도에서 시기 자체가 달라졌을 때만 쓴다.
    """
    props = row["props"]
    now_rank = RANK.get(row["정밀도"], 0)
    new_rank = RANK.get(found["정밀도"], 0)
    if new_rank < now_rank:
        return {}                       # 소스가 더 거칠어졌다 — 무시

    # 영화는 국내 개봉일을 알면 그쪽을 쓴다 (스키마가 국내/해외를 안 나눈다)
    date = found["날짜"]
    if found["정밀도"] == "확정" and found.get("국내날짜"):
        date = found["국내날짜"]

    date = placeholder(date, found["정밀도"])
    if not date:
        return {}

    cur = props["출시·개봉일"]["date"]
    cur = cur["start"][:10] if cur else None
    if new_rank == now_rank and cur == date:
        return {}

    return {
        "출시·개봉일": {"date": {"start": date}},
        "날짜정밀도": {"select": {"name": found["정밀도"]}},
        "마지막확인": {"date": {"start": time.strftime("%Y-%m-%d")}},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="바꾸지 않고 출력만")
    ap.add_argument("--limit", type=int, default=0,
                    help="영상 조회 건수 상한 (0=제한 없음)")
    a = ap.parse_args()

    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)

    rows, skip = read_rows(query_all(ids["work_db"]))
    games = [r for r in rows if "igdb" in r]
    shows = [r for r in rows if "tmdb" in r]
    print(f"미확정 {len(rows)}건 — 게임 {len(games)} / 영상 {len(shows)}")
    print(f"  건너뜀: 확정·완결대기 {skip['확정·완결대기']} / "
          f"외부ID없음 {skip['외부ID없음']} / 종류없음 {skip['종류없음']}")

    found = {}

    # 게임 — IGDB는 한 번에 여러 건을 물어볼 수 있어 싸다
    if games:
        try:
            rel = igdb.releases({r["igdb"] for r in games})
            for r in games:
                if r["igdb"] in rel:
                    found[r["id"]] = rel[r["igdb"]]
            print(f"  IGDB 응답 {len(rel)}건")
        except Exception as e:                    # 한 소스가 죽어도 나머지는 돈다
            print(f"  [경고] IGDB 조회 실패: {e}", file=sys.stderr)

    # 영상 — TMDB는 건당 호출이라 상한을 둘 수 있게 했다
    if shows:
        targets = shows[:a.limit] if a.limit else shows
        ok = 0
        for r in targets:
            media_type, tmdb_id = r["tmdb"]
            try:
                d = tmdb.details(tmdb_id, media_type)
            except Exception as e:
                print(f"  [경고] TMDB {tmdb_id} 실패: {e}", file=sys.stderr)
                continue
            if d:
                found[r["id"]] = d
                ok += 1
        print(f"  TMDB 응답 {ok}건 / 조회 {len(targets)}건")

    changed = 0
    for r in rows:
        d = found.get(r["id"])
        if not d:
            continue
        new = plan(r, d)
        if not new:
            continue
        changed += 1
        when = new["출시·개봉일"]["date"]["start"]
        print(f"   {r['제목'][:28]:28} {r['정밀도']} → {d['정밀도']:4} {when}")
        if not a.dry:
            patch(r["id"], new)
            time.sleep(0.34)

    print(f"\n{'갱신 예정' if a.dry else '갱신'} {changed}건")
    if changed and not a.dry:
        print("변화는 track.py가 감지해 알림 카드로 내보낸다")


if __name__ == "__main__":
    main()
