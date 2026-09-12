# -*- coding: utf-8 -*-
"""외부 소스에서 출시일을 다시 물어와 노션에 반영한다.

`track.py`는 '노션이 어제와 달라졌는가'만 본다. 그래서 이 단계가 없으면
바깥에서 날짜가 확정돼도 노션은 그대로고, `[날짜확정]` 알림은 사람이 직접
노션을 고쳤을 때만 울린다. 매일 실행에서 `track.py` **앞에** 두어야
같은 날 알림으로 이어진다.

`sync_series.py`가 이미 도는 작품은 방영 시작 이후를 본다(완결 추적).
이 스크립트는 그 앞 구간 — **아직 안 나온 것의 출시일** — 을 맡는다.

건드리는 범위를 좁게 잡았다.

- 날짜정밀도가 `확정`인 행은 **고치지 않되, 나오기 전까지 지켜본다**.
  소스가 다른 날짜를 말하면 노션은 그대로 두고 알림만 보낸다(연기 감시).
  08-21에 자동 덮어쓰기로 손으로 찍은 6건이 날아간 적이 있어, 사람이 정한
  값을 소스가 덮는 경로는 두지 않는다. 반영은 사람이 결정한다
- `완결대기`는 아예 보지 않는다. `sync_series.py`가 맡는 구간이다
- 정밀도가 **올라갈 때만** 쓴다 (미정 → 연도 → 분기 → 월 → 확정)
- `월`·`분기`·`연도`면 출시·개봉일에 **그 구간의 마지막날**을 넣는다.
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
import calendar
import datetime
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request

import confirm_queue
import notify
from adapters import igdb, tmdb
from config import API, headers

IDS_FILE = "db_ids.json"

# 낮을수록 거칠다. 올라갈 때만 쓴다.
RANK = {"미정": 0, "연도": 1, "분기": 2, "월": 3, "확정": 4}
# 이 값은 아예 보지 않는다. 완결 추적은 `sync_series.py`의 일이다.
SKIP_PRECISION = ("완결대기",)
# 연기 감시로 이미 알린 날짜를 적어둔다. 없으면 같은 연기를 매일 다시 알린다.
STATE_FILE = "refresh_state.json"
# 이 일수 미만의 차이는 알리지 않는다.
#
# 첫 실행에서 6건이 걸렸는데 그중 5건이 1~6일짜리 "앞당김"이었다 (듄3 3일,
# 어벤져스 2일, 페이블 6일…). 소스가 주는 날짜는 대개 해외 기준이고 노션에는
# 국내 개봉일이 들어 있어서, 진짜 변경이 아니라 지역 차이가 그대로 잡힌 것이다.
# 둘을 구분할 방법이 없으므로 며칠짜리는 접는다. 실제 연기는 주 단위로 밀린다
# — 같은 실행에서 팬텀블레이드 제로가 50일 연기로 걸렸고, 그런 건 남는다.
MIN_SHIFT = 7


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


def _last_day(year, month):
    return "%s-%02d-%02d" % (year, month, calendar.monthrange(int(year), month)[1])


def placeholder(date, precision):
    """확정이 아닌 정밀도에서 출시·개봉일에 넣을 자리표시 날짜 (구간의 마지막날).

    첫날이 아니라 마지막날인 이유는 틀리는 방향을 고르는 문제다. "8월 공개"를
    8월 1일로 적으면 8월 2일부터 시스템은 이걸 이미 지난 날짜로 읽는다. 아직
    안 나온 작품이 한 달 내내 "나온 것" 취급을 받는다.

    마지막날로 적으면 반대로 그 달 내내 "아직"으로 남는다. 그리고 8월 공개가
    8월 말까지 확정되지 않는 일은 거의 없다 — 회사가 공지조차 못 할 상황이
    아닌 이상 그 안에 날짜가 나오고, 정밀도가 확정으로 올라가며 이 값은
    사라진다. 자리표시는 채워지기 전까지만 버티면 되는 값이다.
    """
    if not date:
        return None
    year, month = date[:4], int(date[5:7])
    if precision == "확정":
        return date
    if precision == "월":
        return _last_day(year, month)
    if precision == "분기":
        return _last_day(year, (month - 1) // 3 * 3 + 3)
    if precision == "연도":
        return "%s-12-31" % year
    return None


def read_rows(pages, today):
    """조회 대상만 추린다.

    두 종류가 섞여 나온다. `감시`가 False면 정밀도를 올려 노션에 쓰는 대상이고,
    True면 이미 확정이라 **읽기만** 하는 대상이다(연기 감시). 이미 나온 것은
    지켜볼 이유가 없어 뺀다 — 연기란 나오기 전에만 성립한다.

    반환: (대상 목록, 건너뛴 이유별 집계)
    """
    rows, skip = [], {"완결대기": 0, "외부ID없음": 0, "종류없음": 0, "이미 나옴": 0}
    for p in pages:
        props = p["properties"]
        kind = props["종류"]["select"]
        if not kind:
            skip["종류없음"] += 1
            continue
        precision = props["날짜정밀도"]["select"]
        precision = precision["name"] if precision else "미정"
        if precision in SKIP_PRECISION or precision not in RANK:
            skip["완결대기"] += 1
            continue

        cur_date = props["출시·개봉일"]["date"]
        cur_date = cur_date["start"][:10] if cur_date else None
        watch = precision == "확정"
        if watch and (not cur_date or cur_date < today):
            skip["이미 나옴"] += 1
            continue

        ext = txt(props["외부ID"])
        row = {"id": p["id"], "제목": txt(props["제목"]), "종류": kind["name"],
               "정밀도": precision, "props": props,
               "감시": watch, "날짜": cur_date}

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


def load_state():
    try:
        with io.open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (IOError, ValueError):
        return {}


def save_state(state):
    with io.open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")


def source_date(found):
    """소스가 말하는 날짜. 영화는 국내 개봉일을 알면 그쪽 — `plan()`과 같은 규칙."""
    if found["정밀도"] == "확정" and found.get("국내날짜"):
        return found["국내날짜"]
    return found.get("날짜")


def report(delayed, today, dry):
    """소스가 더 뒤의 날짜를 말한다 - 연기 감시.

    고쳐주지 않는 것이 핵심이다. 08-21에 소스가 사람이 정한 값을 덮어써
    손으로 찍은 6건이 날아갔다. 무엇이 달라졌는지만 확인 큐에 넣고,
    반영은 사람이 채팅으로 "q번호 반영" 이라고 답할 때까지 기다린다.

    예전엔 여기서 바로 notify.send_card를 불러 그날 한 번만 알렸다.
    그러면 카드를 못 보고 지나친 날 그 사실이 영영 사라졌다 — 확인 큐로
    옮긴 이유가 이거다. 큐에만 넣어두면 `notify_confirm_queue.py`가
    답할 때까지 매일 다시 알린다.
    """
    state = confirm_queue.load()
    added = []
    for row, src in delayed:
        gap = (datetime.date.fromisoformat(src)
              - datetime.date.fromisoformat(row["날짜"])).days
        move = f"{abs(gap)}일 {'연기' if gap > 0 else '앞당김'}"
        item = confirm_queue.add(state, {
            "키": f"{row['id']}:{src}",
            "질문": f"{row['제목']} 출시일 변경 — 반영할까요?",
            "근거": [f"{row['종류']} · 노션 {row['날짜']} → 소스 {src} · {move}"],
            "db": "work_db",
            "페이지ID": row["id"],
            "패치": {"출시·개봉일": {"date": {"start": src}},
                    "마지막확인": {"date": {"start": today}}},
        }, today)
        if item:
            added.append(item)

    if dry:
        print("\n" + "[확인 큐에 넣을 예정] " +
              ", ".join(c["질문"] for c in added or
                       [{"질문": f"{r['제목']} ({s})"} for r, s in delayed]))
        return
    if added:
        confirm_queue.save(state)
        print(f"확인 큐에 {len(added)}건 추가 (알림은 notify_confirm_queue.py가 낸다)")


def report_checks(checks, today, dry):
    """소스가 말하는 날짜가 이미 지났다 - 확인 필요.

    "앞당겨졌다"고 단정하면 거짓말이 된다. 지나간 날로 앞당겨질 수는 없다.
    2026-09-08에 인턴을 "12일 앞당김 / 공개까지 -4일"로 알렸는데, 실제로는
    소스가 유료 시사회 날짜를 준 것이었다.

    그렇다고 조용히 버리면 안 된다. 노션 날짜가 지나면 그 행은 감시에서
    빠지므로, 정말로 공개를 놓친 경우 이 알림이 그것을 알 수 있는 유일한
    신호다. 놓친 작품을 찾는 것이 이 시스템의 1순위 목적이다.

    그래서 방향을 단정하는 대신 두 날짜를 나란히 놓고 확인을 요청한다.
    한 번 보내고 마는 대신 확인될 때까지 매일 다시 보낸다 - 아침 카드
    한 장을 못 보고 넘기면 다시는 묻지 않기 때문이다.
    """
    d0 = datetime.date.fromisoformat(today)
    summary, details = [], []
    for row, src, days in checks:
        ago = (d0 - datetime.date.fromisoformat(src)).days
        summary.append(f"{row['제목']} — 소스 기준 {src}")
        details.append((f"[확인 필요] {row['제목']}", [
            f"{row['종류']} · 노션 {row['날짜']} · 소스 {src} ({ago}일 전)",
            "소스가 말하는 날짜가 이미 지났습니다. 시사회·선공개일 수도 있고,"
            " 실제 공개를 놓친 것일 수도 있습니다",
            "노션은 변경하지 않았습니다. 확인 후 반영하시려면 채팅으로 알려주세요",
            f"확인될 때까지 매일 안내합니다 ({days}일째)",
        ]))

    if dry:
        print("")
        print("[확인 필요 예정] " + ", ".join(summary))
        return
    notify.send_card(f"확인 필요 {len(checks)}건",
                     summary=["[확인 필요] " + ", ".join(summary)],
                     details=details, kinds=["확인필요", "날짜변경"],
                     count=len(checks))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="바꾸지 않고 출력만")
    ap.add_argument("--limit", type=int, default=0,
                    help="영상 조회 건수 상한 (0=제한 없음)")
    a = ap.parse_args()

    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)

    today = time.strftime("%Y-%m-%d")
    rows, skip = read_rows(query_all(ids["work_db"]), today)
    games = [r for r in rows if "igdb" in r]
    shows = [r for r in rows if "tmdb" in r]
    n_watch = sum(1 for r in rows if r["감시"])
    print(f"조회 {len(rows)}건 — 게임 {len(games)} / 영상 {len(shows)}"
          f"  (미확정 {len(rows) - n_watch} / 연기 감시 {n_watch})")
    print(f"  건너뜀: 완결대기 {skip['완결대기']} / 이미 나옴 {skip['이미 나옴']} / "
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

    changed, delayed, checks = 0, [], []
    state = load_state()
    sent = state.setdefault("알림", {})

    for r in rows:
        d = found.get(r["id"])
        if not d:
            continue

        if r["감시"]:
            # 확정된 행은 고치지 않는다. 소스가 다른 날을 말할 때만 적어둔다.
            src = source_date(d)
            if not src or d["정밀도"] != "확정" or src == r["날짜"]:
                continue
            past = src < today
            # 지나간 날짜는 차이의 크기와 무관하게 묻는다. 며칠짜리여도
            # "이미 나왔다"는 뜻이라 지역 차이로 넘길 수 없다.
            if not past and abs((datetime.date.fromisoformat(src)
                    - datetime.date.fromisoformat(r["날짜"])).days) < MIN_SHIFT:
                continue
            prev = sent.get(r["id"])
            if isinstance(prev, str):      # 옛 형식 - 날짜만 적혀 있다
                prev = {"날짜": prev, "처음": today}
            same = bool(prev) and prev.get("날짜") == src
            if past:
                # 확인될 때까지 매일 다시 알린다. 카드 한 장을 놓치면
                # 다시 묻지 않으므로 한 번만 보내서는 안 된다.
                first = prev["처음"] if same else today
                checks.append((r, src, (datetime.date.fromisoformat(today)
                                        - datetime.date.fromisoformat(first)).days + 1))
                sent[r["id"]] = {"날짜": src, "처음": first}
                continue
            if same:
                continue          # 같은 연기를 매일 다시 알리지 않는다
            delayed.append((r, src))
            sent[r["id"]] = {"날짜": src, "처음": today}
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

    print(f"\n{'갱신 예정' if a.dry else '갱신'} {changed}건 / 연기 {len(delayed)}건 / 확인 필요 {len(checks)}건")
    if changed and not a.dry:
        print("변화는 track.py가 감지해 알림 카드로 내보낸다")

    # 나온 작품의 기록은 남겨둘 이유가 없다. 두면 파일이 계속 자란다.
    alive = {r["id"] for r in rows if r["감시"]}
    state["알림"] = {k: v for k, v in sent.items() if k in alive}
    if delayed:
        report(delayed, today, a.dry)
    if checks:
        report_checks(checks, today, a.dry)
    if not a.dry:
        save_state(state)


if __name__ == "__main__":
    main()
