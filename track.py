# -*- coding: utf-8 -*-
"""매일 도는 추적 스크립트.

1. 노션 작품 DB를 읽어 오늘 상태를 스냅샷으로 저장
2. 어제 스냅샷과 대조해 변화를 찾음
3. 변화가 있으면 노션 알림 DB에 카드 한 장

날짜·정밀도 변화가 이 프로젝트의 핵심이다. 가격·게임패스는 이후 단계.

알림은 (요약 → 상세) 한 카드로 나간다. 요약은 제목만, 상세는 언제부터 볼 수
있는지까지. 문구는 describe.py가 만든다.

사용법:
    python track.py --dry     # 알림 보내지 않고 결과만
    python track.py
"""
import argparse
import datetime
import io
import json
import os
import time
import urllib.error
import urllib.request

import describe
import notify
from config import API, headers

IDS_FILE = "db_ids.json"
SNAP_DIR = "snapshots"

# `게임패스` 체크박스는 2026-08-23에 지웠다. 같은 사실을 소유처가 들고 있고,
# 입점·이탈 알림은 sync_gamepass.py가 직접 만든다.
WATCH = ["출시·개봉일", "날짜정밀도", "진행도(게임)", "진행도(영상)",
         "방영상태"]
# 진행도는 매체별로 속성이 갈려 있다. 한 행은 둘 중 하나만 쓴다.
STAGE = describe.STAGE
# 아직 보거나 하지 않은 단계. 이용 가능일이 지나면 백로그로 넘어가는 것들이다.
# 아직 안 했고 앞으로 할 것. `구매 보류`(안 사기로)와 `시청 보류`는 제외 쪽이라 뺀다.
UNSEEN = ("출시 대기", "구매 대기", "보유함", "공개 대기", "시청 안함")
# 지금 붙잡고 있는 단계. track 자신은 안 쓰지만 `briefing.py`가 쓴다 — 08-21에
# 방치 알림을 걷어내며 여기서 같이 지웠고, 브리핑이 그날부터 매일 죽고 있었다.
PLAYING = ("진행 중", "시청 중")
# 출시일이 지났는데도 대기 상태로 남아 있는 행을 다음 단계로 민다.
# `출시 대기`는 "아직 안 나왔다"는 뜻인데, 나온 뒤에도 그대로면 기대작 뷰에
# 계속 눌러앉고 백로그로는 넘어가지 않는다. 날짜가 이미 말해주는 걸 사람이
# 손으로 옮기고 있었다.
PROMOTE = {
    "진행도(게임)": ("출시 대기", "구매 대기"),
    "진행도(영상)": ("공개 대기", "시청 안함"),
}
# 스냅샷에는 담되 그 자체로는 알림을 만들지 않는 것 (문구를 만들 때 쓴다).
# `작성자`·`수정자`·`생성일`·`수정일`은 속성이 아니라 페이지 값이라 snapshot()이
# 직접 담는다 — 여기 적으면 안 된다
EXTRA = ["마지막플레이일", "플레이시간", "방영진행", "이용 가능일", "완결일",
         "공개처", "플랫폼", "소유처", "소개"]
# `신규`는 스냅샷에 없는 행을 말한다. 그런데 스냅샷이 낡으면(로컬에서 돌렸거나
# 워크플로가 하루 걸렀거나) 며칠 전에 넣은 작품이 다시 신규로 뜬다. 실제로
# 같은 시리즈 3건이 이틀 연속 신규로 울렸다. 노션의 생성 시각을 같이 보고,
# 이 기간이 지난 행은 스냅샷에 없어도 신규로 치지 않는다.
NEW_DAYS = 2

# 카드 요약에서 이 순서로 묶는다. 앞쪽이 더 중요한 것.
KIND_ORDER = ["완결", "오늘 공개", "날짜확정", "게임패스", "방영", "취소",
              "날짜변경", "신규", "전환", "상태변경"]


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


def patch_page(pid, props):
    req = urllib.request.Request(f"{API}/pages/{pid}",
                                 data=json.dumps({"properties": props}).encode(),
                                 headers=headers(), method="PATCH")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError("재시도 초과")


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


def stage_of(row):
    """이 행이 실제로 쓰는 진행도 값. 게임이든 영상이든 하나만 차 있다."""
    return describe.stage_of(row)


def event(kind, row, note=None, urgent=False):
    """알림 한 건. 요약과 상세는 카드를 만들 때 뽑으므로 재료만 담는다."""
    return {"kind": kind, "title": row["제목"], "row": row,
            "note": note, "urgent": urgent}


# `방치` 알림(진행 중인데 60일 넘게 플레이 기록 없음)은 뺐다.
# 진행 중인 게임을 하는 도중에 다른 게임을 들이미는 알림이라, 손이 가지 않는
# 쪽으로만 작용했다. 게다가 판정 대상인 `진행 중`의 대부분은 사람이 찍은 게
# 아니라 sync_steam이 플레이 기록으로 자동으로 넣던 값이었다 — 그 규칙을
# 없앤 지금은 대상 자체가 거의 남지 않는다. 안 한 게 뭔지는 작품 DB를
# 직접 보면 되고, 백로그 추천(available_check·briefing)이 그 자리를 맡는다.


def available_check(cur, today=None):
    """오늘부터 볼 수 있게 된 것 — '아 맞다 이거' 하라고 그날 한 번 알린다.

    이용 가능일이 지나면 출시 대기 작품은 조용히 과거로 밀려서 뷰에서 흐려진다.
    그날 알림 한 번이 그걸 백로그로 넘겨준다.
    """
    stamp = (today or datetime.date.today()).isoformat()
    out = []
    for row in cur.values():
        if stage_of(row) not in UNSEEN:
            continue
        d = row.get("이용 가능일")
        if d and d[:10] == stamp:
            out.append(event("오늘 공개", row, urgent=True))
    return out


def released(row, today):
    """이 행이 정말 나왔는가. 추정 날짜로는 옮기지 않는다.

    날짜정밀도가 `월`이나 `분기`면 출시·개봉일은 그 구간의 마지막날이 들어 있는
    자리표시자다. 그걸 지났다고 대기를 풀면 아직 안 나온 게임이 구매 대기로
    올라간다. 확정된 날짜만 근거로 쓴다.
    """
    av = row.get("이용 가능일")
    if not av or av[:10] > today.isoformat():
        return False
    if row.get("날짜정밀도") == "완결대기":
        # 완결일은 남은 화수 × 7일 추정일 수 있다. 실제로 끝났는지는 방영상태가 말한다
        return row.get("방영상태") in ("완결", "시즌완결")
    return row.get("날짜정밀도") == "확정"


def promote_stage(cur, today=None, dry=False):
    """출시일이 지난 대기 행을 다음 단계로 옮긴다 (노션에 직접 쓴다).

    `cur`도 같이 고쳐야 한다. 스냅샷에 옛 값이 남으면 내일 대조에서 우리가 쓴
    값이 `[상태변경]`으로 되돌아온다 — 자기가 한 일을 자기가 알리는 꼴이다.
    """
    today = today or datetime.date.today()
    out = []
    for pid, row in cur.items():
        for prop, (before, after) in PROMOTE.items():
            if row.get(prop) != before:
                continue
            if not released(row, today):
                break
            if not dry:
                patch_page(pid, {prop: {"select": {"name": after}}})
                time.sleep(0.34)
            row[prop] = after
            # 오늘 나온 건 available_check가 [오늘 공개]로 이미 알린다.
            # 여기서 또 세면 같은 작품이 카드에 두 줄로 뜬다
            if (row.get("이용 가능일") or "")[:10] != today.isoformat():
                out.append(event("전환", row, note=f"진행도 {before} → {after}"))
            break
    return out


def user_names():
    """노션 사용자 id → 이름. 워크스페이스에 사람 하나와 봇 몇이라 한 번이면 된다.

    스크립트가 쓴 행은 `출시 트래커`(워크플로가 쓰는 통합 토큰)로 찍힌다.
    """
    try:
        req = urllib.request.Request(f"{API}/users", headers=headers())
        with urllib.request.urlopen(req) as r:
            return {u["id"]: (u.get("name") or u["id"][:8])
                    for u in json.loads(r.read())["results"]}
    except Exception as e:
        print(f"[경고] 사용자 목록을 못 읽었습니다 — 출처 줄은 생략합니다: {e}")
        return {}


def snapshot():
    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)
    names = user_names()
    out = {}
    for p in query_all(ids["work_db"]):
        props = p["properties"]
        row = {"제목": value(props["제목"]), "종류": value(props["종류"]),
               "생성일": p["created_time"][:10],
               "수정일": p["last_edited_time"][:10]}
        # `작성자`·`수정자`는 노션 속성이 아니라 페이지 자체의 값이라 따로 담는다.
        # 나중에 "이 행이 왜 이래?"를 되짚을 때 스크립트가 넣은 것인지 사람이
        # 넣은 것인지가 갈림길이 된다.
        for key, src in (("작성자", "created_by"), ("수정자", "last_edited_by")):
            uid = (p.get(src) or {}).get("id")
            if uid and names:
                row[key] = names.get(uid, uid[:8])
        for k in WATCH + EXTRA:
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


def diff(prev, cur, today=None):
    """어제와 오늘의 차이를 알림 이벤트 목록으로."""
    today = today or datetime.date.today()
    events = []
    for pid, now in cur.items():
        old = prev.get(pid)
        if old is None:
            born = now.get("생성일")
            if born:
                try:
                    if (today - datetime.date.fromisoformat(born)).days > NEW_DAYS:
                        continue      # 스냅샷이 낡은 것뿐이다. 이미 알렸다
                except ValueError:
                    pass
            events.append(event("신규", now))
            continue
        for k in WATCH:
            if k not in old or k not in now:
                # 속성이 생기거나 없어진 첫날. 어느 쪽이든 전 행이 알림으로 터진다.
                #
                # 2026-08-23에 실제로 났다. `게임패스` 체크박스를 지웠더니
                # False -> None이 되어 685행이 전부 아래 else로 떨어졌고,
                # kind가 "게임패스"라 describe가 "게임패스에 들어왔다"로 읽었다.
                # 스키마에서 사라진 값은 변화가 아니라 변화를 볼 수 없게 된 것이다.
                continue
            a, b = old.get(k), now.get(k)
            if a == b:
                continue
            if k == "방영상태":
                # 완결을 기다리던 작품이 끝나는 순간이 이 프로젝트에서 제일 중요한 알림.
                # 나무위키 방영표를 매주 열어보던 걸 대신한다.
                #
                # 단, 값이 없다가 처음 채워진 건 알림 대상이 아니다.
                # 속성을 새로 만든 날 과거 완결작 30건이 한꺼번에 터진다.
                if a is None:
                    continue
                if b in ("완결", "시즌완결"):
                    waiting = now.get("날짜정밀도") == "완결대기"
                    events.append(event(
                        "완결", now,
                        # 문구는 describe가 만든다. 여기서는 사실만 넘긴다.
                        note=None if waiting else f"방영상태 {a} → {b}",
                        urgent=waiting))
                elif b == "취소":
                    events.append(event("취소", now, note="시리즈 중단"))
                else:
                    events.append(event("방영", now, note=f"방영상태 {a} → {b}"))
            elif k == "날짜정밀도" and b == "확정":
                events.append(event("날짜확정", now,
                                    note=f"날짜정밀도 {a} → 확정", urgent=True))
            elif k == "게임패스" and b:
                events.append(event("게임패스", now, urgent=True))
            elif k == "출시·개봉일":
                events.append(event("날짜변경", now, note=f"날짜 {a} → {b}"))
            elif k in STAGE:
                events.append(event("상태변경", now, note=f"진행도 {a} → {b}"))
            else:
                events.append(event(k, now, note=f"{k} {a} → {b}"))
    return events


# ─────────────────────────────────────────────
# 카드 만들기
# ─────────────────────────────────────────────
def group(events):
    """종류별로 묶는다. KIND_ORDER에 없는 종류는 뒤로 밀어 둔다."""
    order = {k: i for i, k in enumerate(KIND_ORDER)}
    kinds = sorted({e["kind"] for e in events},
                   key=lambda k: (order.get(k, len(order)), k))
    return [(k, [e for e in events if e["kind"] == k]) for k in kinds]


def build_card(events, today=None):
    """(제목, 요약, 상세, 종류들). 요약은 제목만, 상세는 날짜와 할 일까지."""
    today = today or datetime.date.today()
    urgent = [e for e in events if e["urgent"]]

    if urgent:
        title = f"지금 확인 {len(urgent)}건"
        if len(events) > len(urgent):
            title += f" · 그 외 변경 {len(events) - len(urgent)}건"
    else:
        title = f"오늘의 변경 {len(events)}건"

    grouped = group(events)
    summary = [f"[{kind}] " + ", ".join(e["title"] for e in g)
               for kind, g in grouped]

    # 상세는 급한 것부터. 그 안에서는 요약과 같은 순서라 위아래로 찾아가기 쉽다.
    ordered = [e for _, g in grouped for e in g]
    ordered.sort(key=lambda e: not e["urgent"])
    details = [(f"[{e['kind']}] {e['title']}",
                describe.detail(e["kind"], e["row"], e["note"], today))
               for e in ordered]

    return title, summary, details, [k for k, _ in grouped]


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
        events = []
    else:
        prev_data, prev_name = prev
        events = diff(prev_data, cur, datetime.date.today())
        print(f"{prev_name}과 대조 — 변경 {len(events)}건")

    # 전환은 대조가 끝난 뒤에 한다. 먼저 하면 우리가 쓴 값이 그대로
    # [상태변경] 알림이 되고, 스냅샷에는 전환 후 값이 담겨야 내일 조용하다.
    events += promote_stage(cur, datetime.date.today(), a.dry)

    # --dry는 아무것도 바꾸지 않는다 (스냅샷을 덮어쓰면 기준값이 사라진다)
    if not a.dry:
        os.makedirs(SNAP_DIR, exist_ok=True)
        with io.open(os.path.join(SNAP_DIR, f"{today}.json"), "w",
                     encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)

    events += available_check(cur)

    if not events:
        print("변경 없음 — 알림 보내지 않습니다.")
        return

    title, summary, details, kinds = build_card(events)
    print(f"\n{notify.card_title(title, today)}")
    for s in summary:
        print("  " + s)
    print()
    for headline, lines in details:
        print("  " + headline)
        for line in lines:
            print("      " + line)

    if a.dry:
        return
    notify.send_card(title, summary, details, kinds=kinds, count=len(events),
                     date=today)
    if not notify.spooling():
        print("\n알림 카드 1장 발송 완료")


if __name__ == "__main__":
    main()
