# -*- coding: utf-8 -*-
"""알림 카드 견본 — 각 알림 종류가 어떻게 조립되는지 눈으로 본다.

**노션에는 아무것도 보내지 않는다.** 문구를 손볼 때마다 먼저 돌린다.

견본은 실제 노션 행을 읽어오되, 그 종류의 알림이 울리는 순간의 상태를 만들려고
값을 몇 개 덮어쓴다(완결일이 지나야 완결이 되는 식이라 안 그러면 조건이 안 맞는다).
**덮어쓴 값은 출력에 `[가정]`으로 표시한다.** 이걸 안 적었더니 견본을 실제 알림으로
읽고 "컬트오브램이 왜 방치냐"는 소리가 나왔다. 견본은 형식을 보는 물건이지
내용을 보는 물건이 아니다.

사용법:
    python preview_card.py
"""
import datetime

import describe
import notify
import track

cur = track.snapshot()
by_title = {v["제목"]: v for v in cur.values()}
TODAY = datetime.date.today()
FAKE = {}


def pick(title, **patch):
    row = dict(by_title[title])
    if patch:
        FAKE[title] = patch
    row.update(patch)
    return row


events = [
    track.event("완결", pick("황천의 츠가이", 방영상태="완결",
                             방영진행="24/24화 · 종영 2026-08-18",
                             완결일="2026-08-18", **{"이용 가능일": "2026-08-18"}),
                urgent=True),
    track.event("오늘 공개", pick("인턴", **{"출시·개봉일": TODAY.isoformat(),
                                          "이용 가능일": TODAY.isoformat()}),
                urgent=True),
    track.event("날짜확정", pick("듄3"), note="날짜정밀도 분기 → 확정", urgent=True),
    track.event("게임패스", pick("스토커2", 게임패스=True), urgent=True),
    track.event("날짜변경", pick("서브나우티카2", **{"출시·개봉일": "2026-11-12",
                                                "이용 가능일": "2026-11-12"}),
                note="날짜 2026-09-30 → 2026-11-12"),
    track.event("신규", pick("랜턴스")),
    track.event("전환", pick("GTA6", **{"진행도(게임)": "구매 대기",
                                       "출시·개봉일": "2026-08-11",
                                       "이용 가능일": "2026-08-11"}),
                note="진행도 출시 대기 → 구매 대기"),
    track.event("상태변경", pick("Core Keeper"), note="진행도 보유함 → 진행 중"),
]

title, summary, details, kinds = track.build_card(events, TODAY)
print("=" * 70)
print("카드 제목:", notify.card_title(title))
print("종류 태그:", " / ".join(kinds), " · 건수", len(events))
print("=" * 70)
if FAKE:
    print("\n[가정] 조건을 맞추려고 덮어쓴 값 — 실제 DB 값이 아니다")
    for t, patch in FAKE.items():
        print(f"   {t}: " + ", ".join(f"{k}={v}" for k, v in patch.items()))
print("\n[요약]")
for s in summary:
    print("  •", s)
print("\n[상세]")
for headline, lines in details:
    print("  " + headline)
    for l in lines:
        print("      " + l)
