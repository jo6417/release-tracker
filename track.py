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

WATCH = ["출시·개봉일", "날짜정밀도", "단계", "게임패스", "국내 출시·개봉일"]
IDLE_DAYS = 60      # 플레이중인데 이 기간 넘게 안 켠 게임은 방치로 본다


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
    return None


def idle_check(cur):
    """플레이중인데 오래 손 안 댄 게임 — 백로그가 조용히 쌓이는 걸 막는다."""
    import datetime
    today = datetime.date.today()
    out = []
    for row in cur.values():
        if row.get("단계") != "플레이중":
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
        for k in ("마지막플레이일", "플레이시간"):
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
            a, b = old.get(k), now.get(k)
            if a == b:
                continue
            title = now["제목"]
            if k == "날짜정밀도" and b == "확정":
                urgent.append(f"[날짜확정] {title} — {now.get('출시·개봉일')}")
            elif k == "게임패스" and b:
                urgent.append(f"[게임패스] {title} 입점")
            elif k == "출시·개봉일":
                daily.append(f"[날짜변경] {title}: {a} → {b}")
            elif k == "단계":
                daily.append(f"[단계] {title}: {a} → {b}")
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
