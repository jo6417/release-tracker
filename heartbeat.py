# -*- coding: utf-8 -*-
"""월 1회 생존 신호 — 시스템이 조용히 죽는 것을 막기 위한 장치.

GitHub은 public 리포에 60일간 활동이 없으면 예약 워크플로를 자동으로 끈다.
워크플로가 스스로 만든 커밋은 활동으로 쳐주지 않는 것으로 알려져 있어,
자동 커밋으로 타이머를 되돌리는 방법은 통하지 않는다.

그래서 막는 대신 들키게 만든다. 매월 1일에 알림을 한 건 보내고,
그 알림이 오지 않는 달이 있으면 시스템이 멈춘 것이다.
(Actions 탭에서 다시 켜면 복구된다)

알림이 무시당하면 장치로서 쓸모가 없으므로, 읽을 이유가 있는 내용을 담는다.

사용법:
    python heartbeat.py          # 오늘이 1일일 때만 보냄
    python heartbeat.py --force  # 날짜 무관하게 보냄 (테스트용)
"""
import argparse
import datetime
import io
import json
import urllib.request

import notify
from config import API, headers

IDS_FILE = "db_ids.json"
SOON_DAYS = 30


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


def summary():
    """추적 규모와 코앞의 출시를 한 줄로 요약한다."""
    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)

    rows = query_all(ids["work_db"])
    today = datetime.date.today()
    limit = today + datetime.timedelta(days=SOON_DAYS)

    soon = []
    for p in rows:
        pr = p["properties"]
        if (pr["날짜정밀도"]["select"] or {}).get("name") != "확정":
            continue
        d = pr["출시·개봉일"]["date"]
        if not d:
            continue
        try:
            start = datetime.date.fromisoformat(d["start"][:10])
        except ValueError:
            continue
        if today <= start <= limit:
            title = pr["제목"]["title"]
            soon.append((start, title[0]["plain_text"] if title else "(무제)"))

    soon.sort()
    line = f"[점검] 출시 트래커 정상 작동 중 — 작품 {len(rows)}건 추적"
    if soon:
        line += f", 앞으로 {SOON_DAYS}일 내 출시 {len(soon)}건"
        line += "\n" + "\n".join(f"  {d} {t}" for d, t in soon[:5])
    else:
        line += f", 앞으로 {SOON_DAYS}일 내 출시 예정 없음"
    line += "\n이 알림이 오지 않는 달이 있으면 시스템이 멈춘 것입니다."
    return line


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="날짜 무관하게 발송")
    ap.add_argument("--dry", action="store_true", help="보내지 않고 출력만")
    a = ap.parse_args()

    if not a.force and datetime.date.today().day != 1:
        print("오늘은 1일이 아닙니다 — 생존 신호를 보내지 않습니다.")
        return

    text = summary()
    print(text)
    if a.dry:
        print("(--dry: 실제로 보내지 않았습니다)")
        return
    notify.send(text)
    print("생존 신호 발송 완료")


if __name__ == "__main__":
    main()
