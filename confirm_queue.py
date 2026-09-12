# -*- coding: utf-8 -*-
"""확인 큐 — "반영할까요?"를 대답할 때까지 매일 다시 묻는다.

`refresh_dates.py`는 출시일 연기를 그날 한 번만 알리고 끝났다. 바빠서
카드를 지나치면 다음 날 사라져서 영영 반영이 안 됐다. `candidates.py`가
후보를 반복 제시하는 구조를 일반화해, "노션에 반영할까요?" 종류의 질문
전체에 쓴다.

번호(q1, q2…)는 한 번 붙으면 안 바뀐다. 며칠 전 카드를 보고 답해도 같은
것을 가리킨다.

답은 셋뿐이다.
    반영 — 저장해둔 패치를 그대로 적용하고 큐에서 뺀다
    무시 — 아무것도 안 하고 큐에서 뺀다 (다시 안 물어봄)
    나중에 — 7일 뒤 다시 묻는다

큐 항목은 이 모양이다:
    {"키", "종류", "질문", "근거": [...], "db": "work_db", "페이지ID": "...",
     "패치": {...노션 properties 그대로...}, "등록일", "번호"}

`패치`가 없는 항목(예: "받으셨나요?" 같은 순수 확인)도 허용한다 — 그때는
`반영`이 그냥 큐를 비우는 것 이상의 의미가 없고, 실제 처리는 호출부가
`buy.py` 등으로 따로 한다.
"""
import datetime
import io
import json

FILE = "confirm_queue.json"
RETRY_DAYS = 7


def load():
    try:
        with io.open(FILE, encoding="utf-8") as f:
            state = json.load(f)
    except (IOError, ValueError):
        state = {}
    state.setdefault("다음번호", 1)
    state.setdefault("큐", {})
    state.setdefault("처리됨", {})
    return state


def save(state):
    with io.open(FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


def add(state, item, today=None):
    """새 확인 항목을 큐에 넣는다. 같은 `키`가 이미 큐에 있거나 반영·무시로
    처리됐으면 넣지 않는다(같은 사실을 두 번 묻지 않는다). '나중에'는
    처리됨에 안 남으므로 재유입을 막지 않는다 — 큐에 그대로 남아 있다가
    다시물날이 되면 다시 보인다.

    반환: 실제로 들어갔으면 그 항목(번호 포함), 아니면 None
    """
    today = today or datetime.date.today().isoformat()
    if any(c["키"] == item["키"] for c in state["큐"].values()):
        return None
    if state["처리됨"].get(item["키"], {}).get("결정") in ("반영", "무시"):
        return None
    num = "q%d" % state["다음번호"]
    state["다음번호"] += 1
    row = dict(item, 번호=num, 등록일=today, 다시물날=None)
    state["큐"][num] = row
    return row


def pending(state, today=None):
    """오늘 보여줄 항목 전부. `나중에`로 미룬 것 중 기한이 안 된 건 뺀다."""
    today = today or datetime.date.today().isoformat()
    out = [c for c in state["큐"].values()
          if not c.get("다시물날") or c["다시물날"] <= today]
    return sorted(out, key=lambda c: c["등록일"])


def days_pending(item, today=None):
    today = today or datetime.date.today().isoformat()
    d0 = datetime.date.fromisoformat(item["등록일"])
    d1 = datetime.date.fromisoformat(today)
    return (d1 - d0).days + 1


def resolve(state, 번호, 결정, today=None):
    """반영/무시/나중에. 반영·무시는 큐에서 빼고 처리됨에 남긴다.
    나중에는 큐에 두고 다시물날만 미룬다."""
    today = today or datetime.date.today().isoformat()
    row = state["큐"].get(번호)
    if not row:
        return None
    if 결정 == "나중에":
        retry = (datetime.date.fromisoformat(today)
                + datetime.timedelta(days=RETRY_DAYS)).isoformat()
        row["다시물날"] = retry
        return row
    row = state["큐"].pop(번호)
    state["처리됨"][row["키"]] = {"결정": 결정, "질문": row["질문"], "날짜": today}
    return row
