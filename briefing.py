# -*- coding: utf-8 -*-
"""저녁 브리핑 — 매일 19:00 KST 카드 한 장.

아침 09:00의 `track.py`는 **어제와 달라진 것**만 알린다. 변한 게 없으면 아무
알림도 오지 않고, 그래서 "오늘 뭐 볼까"에는 답을 주지 못한다. 이 스크립트는
반대로 **지금 상태**를 매일 같은 시각에 늘어놓는다.

순서에 의도가 있다. 이 프로젝트의 1순위는 "놓치고 넘어간 걸 발견하는 것"이라,
당장 볼 수 있는 것 → 곧 볼 수 있는 것 → 지금 붙잡고 있는 것 → **오늘의 추천**
순으로 내려간다. 마지막 칸은 매일 다른 걸 집어 올린다.

사용법:
    python briefing.py --dry
    python briefing.py
"""
import argparse
import datetime

import describe
import notify
import track

# 임박 펄스 — 아직 안 나온 작품을 D-7과 D-1에 딱 두 번 알린다. D-0은 `오늘 공개`
# 칸이 이미 하고 있어 따로 만들지 않는다.
#
# 예전에는 D+1~D+7을 매일 늘어놓았다. 같은 작품이 이레 내내 같은 자리에 뜨는데,
# 어제 읽은 줄이 오늘 또 오면 그 칸 전체를 넘기게 된다. 세 번만 울리면 그 세 번을
# 읽는다. D-7은 예매·사전구매처럼 미리 해둘 게 걸리는 시점이고, D-1은 "내일이다"다.
PULSE_DAYS = (7, 1)
# 아직 안 나온 상태. 스키마가 게임/영상으로 갈라져 있어 두 값이 같은 뜻이다.
PULSE_STAGES = ("출시 대기", "공개 대기")
ENDING_DAYS = 14     # 완결 임박으로 볼 기간
BACKLOG_PICK = 3     # 하루에 집어 올릴 추천 개수
# 지금 붙잡고 있는 게 있으면 추천을 줄인다. 할 게 밀려 있는데 매일 새 작품을
# 세 개씩 들이미는 건 도움이 아니라 소음이다.
#
# 다만 "얼마나 봤는지"는 게임만 알 수 있다 — 스팀이 마지막플레이일을 준다.
# 영상물에는 그런 기록이 없어서, 증거가 있는 행만 센다. 모르는 걸 넘겨짚어
# 추천을 끄면 조용해진 이유를 알 수 없게 된다. 없으면 평소대로 세 개다.
BACKLOG_PICK_BUSY = 1
BUSY_DAYS = 14       # 이 안에 손댄 기록이 있으면 '한창 진행 중'으로 본다
SOON_MAX = 8
PLAYING_MAX = 15


def _date(iso):
    if not iso:
        return None
    try:
        return datetime.date.fromisoformat(iso[:10])
    except (ValueError, TypeError):
        return None


def _dday(row, today):
    d = _date(row.get("이용 가능일"))
    n = (d - today).days if d else 0
    return "내일" if n == 1 else f"D-{n}"


def _sort_key(row):
    """제목순은 매번 같은 것만 위로 올라온다. 날짜를 1순위로."""
    return (row.get("이용 가능일") or "9999", row.get("제목") or "")


def collect(cur, today):
    """섹션별 작품 목록. 각 섹션은 (라벨, [행]) 이다."""
    unseen, playing = [], []
    for row in cur.values():
        stage = describe.stage_of(row)
        if stage in track.UNSEEN:
            unseen.append(row)
        elif stage in track.PLAYING:
            playing.append(row)

    today_out, soon, ending, backlog = [], [], [], []
    for row in unseen:
        # 완결 기다리는 중인 시리즈는 완결일이 곧 "볼 수 있게 되는 날"이다.
        if row.get("날짜정밀도") == "완결대기":
            fin = _date(row.get("완결일"))
            if fin and 0 <= (fin - today).days <= ENDING_DAYS:
                ending.append(row)
                continue
        av = _date(row.get("이용 가능일"))
        if not av:
            continue
        gap = (av - today).days
        if gap == 0:
            today_out.append(row)
        elif gap > 0:
            # 아직 안 나온 것은 펄스 날에만 알리고, 그 외에는 어느 칸에도 넣지
            # 않는다. 백로그는 "이미 나왔는데 아직 안 한 것"이라, 미출시작이
            # 섞이면 오늘의 추천이 볼 수도 없는 작품을 들이민다.
            #
            # 확정이 아닌 정밀도에서 `출시·개봉일`은 구간의 첫날을 넣어둔
            # 자리표시다. 그걸로 D-day를 세면 "9월 어느 날"이 9월 1일 기준
            # D-7로 둔갑한다. 확정만 센다.
            if (gap in PULSE_DAYS
                    and row.get("날짜정밀도") == "확정"
                    and describe.stage_of(row) in PULSE_STAGES):
                soon.append(row)
        else:
            backlog.append(row)

    playing.sort(key=lambda r: r.get("마지막플레이일") or "", reverse=True)
    return {
        "오늘": sorted(today_out, key=_sort_key),
        "곧": sorted(soon, key=_sort_key)[:SOON_MAX],
        "완결임박": sorted(ending, key=lambda r: r.get("완결일") or ""),
        "진행중": playing[:PLAYING_MAX],
        "백로그": pick_backlog(backlog, today,
                            BACKLOG_PICK_BUSY if busy(playing, today)
                            else BACKLOG_PICK),
    }


def busy(playing, today):
    """최근에 실제로 손댄 기록이 있는가. 게임만 답할 수 있는 질문이다."""
    for row in playing:
        d = _date(row.get("마지막플레이일"))
        if d and 0 <= (today - d).days <= BUSY_DAYS:
            return True
    return False


def pick_backlog(rows, today, count=BACKLOG_PICK):
    """이미 나왔는데 아직 안 본 것 중 몇 개. 매일 다른 것이 올라와야 한다.

    무작위로 뽑으면 재현이 안 돼 디버깅이 괴롭고, 앞에서부터 자르면 같은 작품이
    영원히 1번이다. 날짜를 커서 삼아 목록을 돌린다 — 하루에 BACKLOG_PICK칸씩
    밀리므로 백로그 전체를 한 바퀴 훑게 된다.

    커서는 `count`와 무관하게 늘 BACKLOG_PICK칸씩 민다. 바쁜 날(count=1)은 그
    칸의 첫 하나만 보여주고 나머지 둘은 그냥 지나간다. 커서까지 같이 줄이면
    바쁜 기간에 목록이 느리게 돌아 같은 작품이 며칠씩 다시 올라온다.
    """
    if not rows:
        return []
    rows = sorted(rows, key=_sort_key)
    start = (today.toordinal() * BACKLOG_PICK) % len(rows)
    doubled = rows + rows
    return doubled[start:start + min(count, len(rows))]


def build(sections, today):
    """(라벨, 요약, 상세, 종류들)"""
    # 라벨은 알림 카드의 소제목이다. 구어체("아 맞다 이거")는 한 번은 재밌지만
    # 매일 같은 자리에 뜨면 실없어진다. 무엇을 모아둔 칸인지만 적는다.
    label = {"오늘": "오늘 공개", "곧": "공개 임박",
             "완결임박": "완결 임박", "진행중": "진행 중", "백로그": "오늘의 추천"}
    summary, details, kinds = [], [], []

    for key in ("오늘", "곧", "완결임박", "진행중", "백로그"):
        rows = sections[key]
        if not rows:
            continue
        kinds.append("브리핑")
        if key == "곧":
            # 며칠 남았는지가 이 칸의 전부다. 제목만 적으면 D-7인지 내일인지
            # 상세를 열어야 알 수 있다.
            names = ", ".join(f"{r['제목']} ({_dday(r, today)})" for r in rows)
        else:
            names = ", ".join(r["제목"] for r in rows)
        summary.append(f"[{label[key]}] " + names)
        for row in rows:
            details.append((f"[{label[key]}] {row['제목']}",
                            describe.detail(key, row, None, today)))

    n_now = len(sections["오늘"]) + len(sections["완결임박"])
    head = f"오늘의 브리핑 · 지금 이용 가능 {n_now}건" if n_now else "오늘의 브리핑"
    return head, summary, details, kinds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="알림 보내지 않음")
    a = ap.parse_args()

    today = datetime.date.today()
    cur = track.snapshot()
    print(f"작품 {len(cur)}행")

    sections = collect(cur, today)
    if not any(sections.values()):
        print("브리핑할 내용이 없습니다.")
        return

    head, summary, details, kinds = build(sections, today)
    print(f"\n{notify.card_title(head, today.isoformat())}")
    for s in summary:
        print("  " + s)
    print()
    for headline, lines in details:
        print("  " + headline)
        for line in lines:
            print("      " + line)

    if a.dry:
        return
    notify.send_card(head, summary, details, kinds=["브리핑"],
                     count=sum(len(v) for v in sections.values()),
                     date=today.isoformat())
    if not notify.spooling():
        print("\n브리핑 카드 1장 발송 완료")


if __name__ == "__main__":
    main()
