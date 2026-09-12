# -*- coding: utf-8 -*-
"""확인 큐 답을 처리한다 — 세션이 채팅에서 받은 결정을 노션에 반영한다.

`apply_candidates.py`와 같은 자리다. 아침 카드의 [확인 필요] q번호에
사용자가 채팅으로 답하면 이 스크립트를 돌린다.

사용법:
    python answer_confirm.py 반영 q1 q2
    python answer_confirm.py 무시 q3
    python answer_confirm.py 나중에 q4
    python answer_confirm.py --목록
"""
import argparse
import json
import time
import urllib.error
import urllib.request

import confirm_queue
from config import API, headers

DB_ID_KEY = {"work_db": "work_db", "schedule_db": "schedule_db",
             "purchase_db": "purchase_db"}


def patch_page(pid, props):
    req = urllib.request.Request(f"{API}/pages/{pid}",
                                 data=json.dumps({"properties": props}).encode(),
                                 headers=headers(), method="PATCH")
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"[에러] {e.code}: {e.read().decode()[:400]}")
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("결정", nargs="?", choices=["반영", "무시", "나중에"])
    ap.add_argument("번호", nargs="*")
    ap.add_argument("--목록", action="store_true")
    a = ap.parse_args()

    state = confirm_queue.load()

    if a.목록 or not a.결정:
        today = time.strftime("%Y-%m-%d")
        rows = confirm_queue.pending(state, today)
        print(f"확인 필요 {len(rows)}건")
        for c in rows:
            print(f"  {c['번호']:>4} {c['질문'][:50]:50} · {confirm_queue.days_pending(c, today)}일째")
        return

    today = time.strftime("%Y-%m-%d")
    for num in a.번호:
        row = confirm_queue.resolve(state, num, a.결정, today)
        if not row:
            print(f"  {num} — 큐에 없다 (이미 처리했거나 없는 번호)")
            continue
        if a.결정 == "반영" and row.get("패치"):
            patch_page(row["페이지ID"], row["패치"])
            print(f"  {num} 반영: {row['질문']}")
        elif a.결정 == "반영":
            print(f"  {num} 확인 처리: {row['질문']} (별도 반영 액션 없음)")
        elif a.결정 == "무시":
            print(f"  {num} 무시: {row['질문']}")
        else:
            print(f"  {num} 나중에 — {row['다시물날']}에 다시 물어봄")

    confirm_queue.save(state)


if __name__ == "__main__":
    main()
