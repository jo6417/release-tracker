# -*- coding: utf-8 -*-
"""매일 도는 추적 스크립트.

1. 노션 작품 DB를 읽어 오늘 상태를 스냅샷으로 저장
2. 어제 스냅샷과 대조해 변화를 찾음
3. 변화가 있으면 노션 알림 DB에 카드 한 장

날짜·정밀도 변화가 이 프로젝트의 핵심이다. 가격·게임패스는 이후 단계.

알림은 (요약 → 상세) 한 카드로 나간다. 요약은 제목만, 상세는 언제부터 볼 수
있는지까지. 문구는 describe.py가 만든다.

사용법:
    python track.py --dry     # 알림 보내지 않고 결과만
    python track.py
"""
import argparse
import datetime
import io
import json
import os
import time
import urllib.request

import describe
import notify
from config import API, headers

IDS_FILE = "db_ids.json"
SNAP_DIR = "snapshots"
STATE_FILE = "notify_state.json"

WATCH = ["출시·개봉일", "날짜정밀도", "진행도(게임)", "진행도(영상)",
         "게임패스", "방영상태"]
# 진행도는 매체별로 속성이 갈려 있다. 한 행은 둘 중 하나만 쓴다.
STAGE = describe.STAGE
# 아직 보거나 하지 않은 단계. 이용 가능일이 지나면 백로그로 넘어가는 것들이다.
# 아직 안 했고 앞으로 할 것. `구매 보류`(안 사기로)와 `시청 보류`는 제외 쪽이라 뺀다.
UNSEEN = ("출시 대기", "구매 대기", "보유함", "공개 대기", "시청 안함")
PLAYING = ("진행 중", "시청 중")
# 스냅샷에는 담되 그 자체로는 알림을 만들지 않는 것 (문구를 만들 때 쓴다)
EXTRA = ["마지막플레이일", "플레이시간", "방영진행", "이용 가능일", "완결일",
         "공개처", "플랫폼", "소유처"]
IDLE_DAYS = 60          # 진행 중인데 이 기간 넘게 안 켠 게임은 방치로 본다
IDLE_REMIND = 30        # 같은 방치를 다시 알리기까지의 간격

# 카드 요약에서 이 순서로 묶는다. 앞쪽이 더 중요한 것.
KIND_ORDER = ["완결", "볼차례", "날짜확정", "게임패스", "방영", "취소",
              "날짜변경", "신규", "진행도", "방치"]


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


def value(prop):
    t = prop["type"]
    if t in ("title", "rich_text"):
        return "".join(x["plain_text"] for x in prop[t])
    if t == "select":
        return prop["select"]["name"] if prop["select"] else None
    if t == "multi_select":
        return [o["name"] for o in prop["multi_select"]]
    if t == "checkbox":
        return prop["checkbox"]
    if t == "number":
        return prop["number"]
    if t == "date":
        d = prop["date"]
        if not d:
            return None
        return d["start"] + (f"~{d['end']}" if d.get("end") else "")
    if t == "formula":
        f = prop["formula"]
        if f["type"] == "date":
            return f["date"]["start"] if f["date"] else None
        return f.get(f["type"])
    return None


def stage_of(row):
    """이 행이 실제로 쓰는 진행도 값. 게임이든 영상이든 하나만 차 있다."""
    return describe.stage_of(row)


def event(kind, row, note=None, urgent=False):
    """알림 한 건. 요약과 상세는 카드를 만들 때 뽑으므로 재료만 담는다."""
    return {"kind": kind, "title": row["제목"], "row": row,
            "note": note, "urgent": urgent}


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    with io.open(STATE_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with io.open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def idle_check(cur, state, today=None):
    """진행 중인데 오래 손 안 댄 게임 — 백로그가 조용히 쌓이는 걸 막는다.

    이 검사는 이전 스냅샷을 보지 않고 현재 상태만 본다. 그대로 두면 같은 방치가
    매일(매시 실행으로 바꾸면 하루 24번) 다시 울린다. 그래서 알린 날짜를
    notify_state.json에 적어두고 IDLE_REMIND일이 지나기 전에는 다시 알리지 않는다.
    """
    today = today or datetime.date.today()
    seen = state.setdefault("idle", {})
    out = []
    for pid, row in cur.items():
        if stage_of(row) not in PLAYING:
            continue
        last = row.get("마지막플레이일")
        if not last:
            continue
        try:
            d = datetime.date.fromisoformat(last[:10])
        except ValueError:
            continue
        gap = (today - d).days
        if gap < IDLE_DAYS:
            seen.pop(pid, None)      # 다시 켰으면 기록을 지운다
            continue
        prev = seen.get(pid)
        if prev:
            try:
                if (today - datetime.date.fromisoformat(prev)).days < IDLE_REMIND:
                    continue
            except ValueError:
                pass
        seen[pid] = today.isoformat()
        out.append(event("방치", row,
                         note=f"진행 중인데 {gap}일째 안 켬 (마지막 {last[:10]})"))
    return out


def available_check(cur, today=None):
    """오늘부터 볼 수 있게 된 것 — '아 맞다 이거' 하라고 그날 한 번 알린다.

    이용 가능일이 지나면 출시 대기 작품은 조용히 과거로 밀려서 뷰에서 흐려진다.
    그날 알림 한 번이 그걸 백로그로 넘겨준다.
    """
    stamp = (today or datetime.date.today()).isoformat()
    out = []
    for row in cur.values():
        if stage_of(row) not in UNSEEN:
            continue
        d = row.get("이용 가능일")
        if d and d[:10] == stamp:
            what = "완결" if row.get("방영상태") in ("완결", "시즌완결") else "공개"
            out.append(event("볼차례", row, note=f"오늘 {what}. 이제 볼 수 있다",
                             urgent=True))
    return out


def snapshot():
    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)
    out = {}
    for p in query_all(ids["work_db"]):
        props = p["properties"]
        row = {"제목": value(props["제목"]), "종류": value(props["종류"])}
        for k in WATCH + EXTRA:
            if k in props:
                row[k] = value(props[k])
        out[p["id"]] = row
    return out


def load_prev():
    if not os.path.isdir(SNAP_DIR):
        return None
    files = sorted(f for f in os.listdir(SNAP_DIR) if f.endswith(".json"))
    if not files:
        return None
    with io.open(os.path.join(SNAP_DIR, files[-1]), encoding="utf-8") as f:
        return json.load(f), files[-1]


def diff(prev, cur):
    """어제와 오늘의 차이를 알림 이벤트 목록으로."""
    events = []
    for pid, now in cur.items():
        old = prev.get(pid)
        if old is None:
            events.append(event("신규", now))
            continue
        for k in WATCH:
            if k not in old:
                continue      # 속성 이름이 바뀐 첫날. 전 행이 알림으로 터지는 걸 막는다
            a, b = old.get(k), now.get(k)
            if a == b:
                continue
            if k == "방영상태":
                # 완결을 기다리던 작품이 끝나는 순간이 이 프로젝트에서 제일 중요한 알림.
                # 나무위키 방영표를 매주 열어보던 걸 대신한다.
                #
                # 단, 값이 없다가 처음 채워진 건 알림 대상이 아니다.
                # 속성을 새로 만든 날 과거 완결작 30건이 한꺼번에 터진다.
                if a is None:
                    continue
                if b in ("완결", "시즌완결"):
                    waiting = now.get("날짜정밀도") == "완결대기"
                    events.append(event(
                        "완결", now,
                        note="기다리던 완결 — 이제 정주행 가능" if waiting
                             else f"방영상태 {a} → {b}",
                        urgent=waiting))
                elif b == "취소":
                    events.append(event("취소", now, note="시리즈 중단"))
                else:
                    events.append(event("방영", now, note=f"방영상태 {a} → {b}"))
            elif k == "날짜정밀도" and b == "확정":
                events.append(event("날짜확정", now,
                                    note=f"날짜 확정 ({a} → 확정)", urgent=True))
            elif k == "게임패스" and b:
                events.append(event("게임패스", now, note="게임패스 입점",
                                    urgent=True))
            elif k == "출시·개봉일":
                events.append(event("날짜변경", now, note=f"날짜 {a} → {b}"))
            elif k in STAGE:
                events.append(event("진행도", now, note=f"진행도 {a} → {b}"))
            else:
                events.append(event(k, now, note=f"{k} {a} → {b}"))
    return events


# ─────────────────────────────────────────────
# 카드 만들기
# ─────────────────────────────────────────────
def group(events):
    """종류별로 묶는다. KIND_ORDER에 없는 종류는 뒤로 밀어 둔다."""
    order = {k: i for i, k in enumerate(KIND_ORDER)}
    kinds = sorted({e["kind"] for e in events},
                   key=lambda k: (order.get(k, len(order)), k))
    return [(k, [e for e in events if e["kind"] == k]) for k in kinds]


def build_card(events, today=None):
    """(제목, 요약, 상세, 종류들). 요약은 제목만, 상세는 날짜와 할 일까지."""
    today = today or datetime.date.today()
    urgent = [e for e in events if e["urgent"]]

    if urgent:
        title = f"지금 확인 {len(urgent)}건"
        if len(events) > len(urgent):
            title += f" · 그 외 변경 {len(events) - len(urgent)}건"
    else:
        title = f"오늘의 변경 {len(events)}건"

    grouped = group(events)
    summary = [f"[{kind}] " + ", ".join(e["title"] for e in g)
               for kind, g in grouped]

    # 상세는 급한 것부터. 그 안에서는 요약과 같은 순서라 위아래로 찾아가기 쉽다.
    ordered = [e for _, g in grouped for e in g]
    ordered.sort(key=lambda e: not e["urgent"])
    details = [(f"[{e['kind']}] {e['title']}",
                describe.detail(e["kind"], e["row"], e["note"], today))
               for e in ordered]

    return title, summary, details, [k for k, _ in grouped]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="알림 보내지 않음")
    a = ap.parse_args()

    today = time.strftime("%Y-%m-%d")
    cur = snapshot()
    print(f"작품 {len(cur)}행 스냅샷")

    prev = load_prev()
    if prev is None:
        print("이전 스냅샷이 없습니다. 오늘 것을 기준으로 저장합니다.")
        events = []
    else:
        prev_data, prev_name = prev
        events = diff(prev_data, cur)
        print(f"{prev_name}과 대조 — 변경 {len(events)}건")

    # --dry는 아무것도 바꾸지 않는다 (스냅샷을 덮어쓰면 기준값이 사라진다)
    if not a.dry:
        os.makedirs(SNAP_DIR, exist_ok=True)
        with io.open(os.path.join(SNAP_DIR, f"{today}.json"), "w",
                     encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)

    events += available_check(cur)

    state = load_state()
    idle = idle_check(cur, state)
    events += idle
    if idle and not a.dry:
        save_state(state)

    if not events:
        print("변경 없음 — 알림 보내지 않습니다.")
        return

    title, summary, details, kinds = build_card(events)
    print(f"\n{notify.card_title(title, today)}")
    for s in summary:
        print("  " + s)
    print()
    for headline, lines in details:
        print("  " + headline)
        for line in lines:
            print("      " + line)

    if a.dry:
        return
    notify.send_card(title, summary, details, kinds=kinds, count=len(events),
                     date=today)
    print("\n알림 카드 1장 발송 완료")


if __name__ == "__main__":
    main()
