# -*- coding: utf-8 -*-
"""아침 알림 카드 견본 — 실제 DB 행에 알림 종류를 하나씩 걸어 조립해 본다.

변화가 없는 날에도 카드 형식을 확인할 수 있다. **노션에는 아무것도 보내지 않는다.**

문구를 손볼 때마다 이걸 먼저 돌린다. 처음 만들 때 이 견본 하나로 네 가지가
잡혔다 — 완결된 작품에 "완결 기다리는 중"이 붙던 것, 진행도 변경에 쓸데없는
출시일이 붙던 것, 게임패스 알림에 정작 게임패스 얘기가 없던 것,
그리고 같은 날짜와 D-day가 두 줄에 걸쳐 반복되던 것.

행은 실제 노션 값을 읽어오고, 알림이 울리는 순간의 상태만 덮어쓴다
(완결일이 지나야 완결이 되는 식이라, 이 덮어쓰기를 빼면 견본이 거짓말을 한다).

사용법:
    python preview_card.py
"""
import io, json, datetime
import notify, track, describe

cur = track.snapshot()
by_title = {v["제목"]: v for v in cur.values()}
TODAY = datetime.date.today().isoformat()


def pick(title, **patch):
    """행을 복사해 '알림이 울리는 순간의 상태'로 만든다."""
    row = dict(by_title[title])
    row.update(patch)
    return row


events = [
    # 완결 대기하던 시리즈가 실제로 끝난 순간
    track.event("완결", pick("황천의 츠가이", 방영상태="완결",
                             방영진행="24/24화 · 종영 2026-08-18",
                             완결일="2026-08-18", **{"이용 가능일": "2026-08-18"}),
                note="완결 기다리던 작품이다", urgent=True),
    # 오늘 개봉해서 볼 차례가 된 것
    track.event("볼차례", pick("인턴", **{"출시·개봉일": TODAY, "이용 가능일": TODAY}),
                note="오늘 공개. 이제 볼 수 있다", urgent=True),
    track.event("날짜확정", pick("듄3"), note="날짜 확정 (분기 → 확정)", urgent=True),
    track.event("게임패스", pick("스토커2", 게임패스=True), note="게임패스 입점", urgent=True),
    track.event("날짜변경", pick("서브나우티카2", **{"출시·개봉일": "2026-11-12",
                                                "이용 가능일": "2026-11-12"}),
                note="날짜 2026-09-30 → 2026-11-12"),
    track.event("신규", pick("랜턴스")),
    track.event("진행도", pick("Core Keeper"), note="진행도 보유함 → 진행 중"),
    track.event("방치", pick("Cult of the Lamb", 마지막플레이일="2026-06-19"),
                note="진행 중인데 61일째 안 켬 (마지막 2026-06-19)"),
]

title, summary, details, kinds = track.build_card(events)
print("=" * 68)
print("카드 제목:", notify.card_title(title))
print("종류 태그:", " / ".join(kinds), " · 건수", len(events))
print("=" * 68)
print("\n[요약]")
for s in summary:
    print("  •", s)
print("\n[상세]")
for headline, lines in details:
    print("  " + headline)
    for l in lines:
        print("      " + l)
