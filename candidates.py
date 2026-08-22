# -*- coding: utf-8 -*-
"""후보 큐 — 발견한 작품을 작품 DB에 바로 넣지 않고 여기 모은다.

이 시스템은 작품 DB에 있는 것만 본다. 그래서 목록에 없는 작품은 트레일러가
나와도, 게임패스에 들어와도 아무 일이 일어나지 않는다(블랙 미스: 종규를
1년 놓친 이유가 그것이다). 발굴을 붙이면 그 구멍은 메워지지만, 찾은 것을
그대로 DB에 밀어 넣으면 이번에는 사람이 분류한 적 없는 행이 쌓인다.
2026-08-21에 sync_steam이 진행도를 덮어써 손으로 찍은 값이 날아간 것과
같은 문제다.

그래서 중간에 큐를 둔다. 발굴은 여기까지만 하고, 작품 DB에 넣을지는
사람이 채팅으로 답한다. 무응답은 보류다 — 지우지 않고 다음 저녁에 다시 묻는다.

번호는 한 번 붙으면 바뀌지 않는다. 매일 1·2·3으로 다시 매기면 어제 카드를
보고 답할 때 엉뚱한 것이 등록된다.
"""
import io
import json

FILE = "candidates.json"

SHOW_PER_DAY = 2      # 저녁 카드에 한 번에 올리는 개수
PENDING_MAX = 5       # 답을 안 준 채 굴러다니는 후보의 상한
# 상한에 닿으면 새 후보를 더 보여주지 않는다. 발굴 자체는 멈추지 않는다 —
# 놓치지 않는 것이 이 프로젝트의 1순위라, 쌓아두더라도 찾기는 계속한다.
# 답을 하면 자리가 비어 새 후보가 올라온다. 출구는 답하는 것뿐이다.


def load():
    try:
        with io.open(FILE, encoding="utf-8") as f:
            state = json.load(f)
    except (IOError, ValueError):
        state = {}
    state.setdefault("다음번호", 1)
    state.setdefault("후보", {})
    state.setdefault("처리됨", {})
    return state


def save(state):
    with io.open(FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


def add(state, items):
    """새 후보를 넣는다. `키`가 이미 큐에 있거나 처리된 적 있으면 무시한다.

    items: [{"키","제목","종류","출처","설명","레퍼런스","외부ID"}]
    반환: 실제로 들어간 후보 목록
    """
    있음 = {c["키"] for c in state["후보"].values()} | set(state["처리됨"])
    새로 = []
    for it in items:
        if it["키"] in 있음:
            continue
        num = "c%d" % state["다음번호"]
        state["다음번호"] += 1
        it = dict(it, 번호=num, 제시횟수=0, 마지막제시=None)
        state["후보"][num] = it
        있음.add(it["키"])
        새로.append(it)
    return 새로


def pick(state):
    """오늘 저녁에 보여줄 후보.

    이미 보여준 것이 상한에 닿으면 그 안에서만 돌린다. 오래 안 보여준 것부터
    올려 같은 둘만 매일 반복되지 않게 한다.
    """
    후보 = list(state["후보"].values())
    보여준 = [c for c in 후보 if c["제시횟수"]]
    pool = 보여준 if len(보여준) >= PENDING_MAX else 후보
    pool = sorted(pool, key=lambda c: (c["마지막제시"] or "", c["번호"]))
    return pool[:SHOW_PER_DAY]


def mark(state, picked, today):
    for c in picked:
        row = state["후보"].get(c["번호"])
        if row:
            row["제시횟수"] += 1
            row["마지막제시"] = today


def resolve(state, 번호, 결정, today):
    """`등록` 또는 `버림`. 큐에서 빼고 처리됨에 남긴다.

    처리됨을 지우지 않는 이유는 재유입 때문이다. 위시리스트는 매일 같은 목록을
    주므로, 한 번 버린 것을 기억하지 않으면 내일 다시 후보로 올라온다.
    """
    row = state["후보"].pop(번호, None)
    if not row:
        return None
    state["처리됨"][row["키"]] = {"결정": 결정, "제목": row["제목"], "날짜": today}
    return row
