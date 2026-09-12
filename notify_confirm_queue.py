# -*- coding: utf-8 -*-
"""확인 큐에 쌓인 것을 하루 한 장으로 알린다.

여러 스크립트(refresh_dates.py 등)가 `confirm_queue.add()`로 항목만
쌓아두고, 실제 알림 발송은 daily.yml에서 이 스크립트가 마지막에 한 번
맡는다 — 스풀에 다른 카드들과 함께 합쳐지게, 알림 순서상 `notify.py
--flush` 바로 앞에 둔다.

사용법:
    python notify_confirm_queue.py --dry
    python notify_confirm_queue.py
"""
import argparse
import time

import confirm_queue
import notify


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    state = confirm_queue.load()
    today = time.strftime("%Y-%m-%d")
    rows = confirm_queue.pending(state, today)

    if not rows:
        print("확인 대기 없음")
        return

    print(f"확인 필요 {len(rows)}건")
    summary = ["[확인 필요] " + ", ".join(
        f"{c['번호']} {c['질문']}" for c in rows)]
    details = []
    for c in rows:
        lines = list(c.get("근거") or [])
        lines.append(f"확인될 때까지 매일 안내합니다 ({confirm_queue.days_pending(c, today)}일째)")
        details.append((f"[확인 필요] {c['번호']} {c['질문']}", lines))

    if a.dry:
        for c in rows:
            print(f"  {c['번호']} {c['질문']}")
        return

    notify.send_card(f"확인 필요 {len(rows)}건", summary=summary,
                     details=details, kinds=["확인필요"], count=len(rows))
    if not notify.spooling():
        print("알림 카드 1장 발송 완료")


if __name__ == "__main__":
    main()
