# -*- coding: utf-8 -*-
"""매일 도는 추적 스크립트.

1. 노션 작품 DB를 읽어 오늘 상태를 스냅샷으로 저장
2. 어제 스냅샷과 대조해 변화를 찾음
3. 변화가 있으면 노션에 알림

날짜·정밀도 변화가 이 프로젝트의 핵심이다. 가격·게임패스는 이후 단계.

사용법:
    python track.py --dry     # 알림 보내지 않고 결과만
    python track.py
"""
import argparse
import io
import json
import os
import time
import urllib.request

import notify
from config import API, headers

IDS_FILE = "db_ids.json"
SNAP_DIR = "snapshots"

WATCH = ["출시·개봉일", "날짜정밀도", "진행도(게임)", "진행도(영상)",
         "게임패스", "방영상태"]
# 진행도는 매체별로 속성이 갈려 있다. 한 행은 둘 중 하나만 쓴다.
STAGE = ("진행도(게임)", "진행도(영상)")
# 아직 보거나 하지 않은 단계. 이용 가능일이 지나면 백로그로 넘어가는 것들이다.
# 아직 안 했고 앞으로 할 것. `구매 보류`(안 사기로)와 `시청 보류`는 제외 쪽이라 뺀다.
UNSEEN = ("출시 대기", "구매 대기", "보유함", "공개 대기", "시청 안함")
PLAYING = ("진행 중", "시청 중")
# 스냅샷에는 담되 그 자체로는 알림을 만들지 않는 것 (문구를 만들 때 쓴다)
EXTRA = ["마지막플레이일", "플레이시간", "방영진행", "이용 가능일"]
IDLE_DAYS = 60      # 진행 중인데 이 기간 넘게 안 켠 게임은 방치로 본다


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


def idle_check(cur):
    """진행 중인데 오래 손 안 댄 게임 — 백로그가 조용히 쌓이는 걸 막는다."""
    import datetime
    today = datetime.date.today()
    out = []
    for row in cur.values():
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
        if gap >= IDLE_DAYS:
            out.append(f"[방치] {row['제목']} — {gap}일째 안 켬 (마지막 {last[:10]})")
    return out


def stage_of(row):
    """이 행이 실제로 쓰는 진행도 값. 게임이든 영상이든 하나만 차 있다."""
    for k in STAGE:
        if row.get(k):
            return row[k]
    return None


def available_check(cur):
    """오늘부터 볼 수 있게 된 것 — '아 맞다 이거' 하라고 그날 한 번 알린다.

    이용 가능일이 지나면 출시 대기 작품은 조용히 과거로 밀려서 뷰에서 흐려진다.
    그날 알림 한 번이 그걸 백로그로 넘겨준다.
    """
    import datetime
    today = datetime.date.today().isoformat()
    out = []
    for row in cur.values():
        if stage_of(row) not in UNSEEN:
            continue
        d = row.get("이용 가능일")
        if d and d[:10] == today:
            what = "완결" if row.get("방영상태") in ("완결", "시즌완결") else "공개"
            out.append(f"[볼차례] {row['제목']} — 오늘 {what}. 이제 볼 수 있다")
    return out


def snapshot():
    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)
    out = {}
    for p in query_all(ids["work_db"]):
        props = p["properties"]
        row = {"제목": value(props["제목"]), "종류": value(props["종류"])}
        for k in WATCH:
            if k in props:
                row[k] = value(props[k])
        for k in EXTRA:
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
    """(즉시 알릴 것, 일일 요약에 넣을 것)"""
    urgent, daily = [], []
    for pid, now in cur.items():
        old = prev.get(pid)
        if old is None:
            daily.append(f"[신규] {now['제목']}")
            continue
        for k in WATCH:
            if k not in old:
                continue      # 속성 이름이 바뀐 첫날. 전 행이 알림으로 터지는 걸 막는다
            a, b = old.get(k), now.get(k)
            if a == b:
                continue
            title = now["제목"]
            if k == "방영상태":
                # 완결을 기다리던 작품이 끝나는 순간이 이 프로젝트에서 제일 중요한 알림.
                # 나무위키 방영표를 매주 열어보던 걸 대신한다.
                #
                # 단, 값이 없다가 처음 채워진 건 알림 대상이 아니다.
                # 속성을 새로 만든 날 과거 완결작 30건이 한꺼번에 터진다.
                if a is None:
                    continue
                if b in ("완결", "시즌완결"):
                    line = f"[완결] {title} — {now.get('방영진행') or ''}".strip()
                    if now.get("날짜정밀도") == "완결대기":
                        urgent.append(line + " · 이제 정주행 가능")
                    else:
                        daily.append(line)
                elif b == "취소":
                    daily.append(f"[취소] {title} — 시리즈 중단")
                elif a is not None:
                    daily.append(f"[방영] {title}: {a} → {b}")
            elif k == "날짜정밀도" and b == "확정":
                urgent.append(f"[날짜확정] {title} — {now.get('출시·개봉일')}")
            elif k == "게임패스" and b:
                urgent.append(f"[게임패스] {title} 입점")
            elif k == "출시·개봉일":
                daily.append(f"[날짜변경] {title}: {a} → {b}")
            elif k in STAGE:
                daily.append(f"[진행도] {title}: {a} → {b}")
            else:
                daily.append(f"[{k}] {title}: {a} → {b}")
    return urgent, daily


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
        urgent, daily = [], []
    else:
        prev_data, prev_name = prev
        urgent, daily = diff(prev_data, cur)
        print(f"{prev_name}과 대조 — 즉시 {len(urgent)}건 / 요약 {len(daily)}건")

    # --dry는 아무것도 바꾸지 않는다 (스냅샷을 덮어쓰면 기준값이 사라진다)
    if not a.dry:
        os.makedirs(SNAP_DIR, exist_ok=True)
        with io.open(os.path.join(SNAP_DIR, f"{today}.json"), "w",
                     encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)

    ready = available_check(cur)
    if ready:
        print(f"볼차례 {len(ready)}건")
        urgent += ready

    idle = idle_check(cur)
    if idle:
        print(f"방치 {len(idle)}건")
        daily += idle

    for line in urgent + daily:
        print("  ", line)

    if a.dry or not (urgent or daily):
        if not (urgent or daily):
            print("변경 없음 — 알림 보내지 않습니다.")
        return

    for line in urgent:
        notify.send(line)
    if daily:
        head = f"오늘의 변경 {len(daily)}건"
        notify.send(head + "\n" + "\n".join(daily[:20]))
    print("알림 발송 완료")


if __name__ == "__main__":
    main()
