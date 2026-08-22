# -*- coding: utf-8 -*-
"""후보 큐의 답을 처리한다 — 세션이 채팅에서 받은 결정을 노션에 반영한다.

저녁 카드는 후보를 번호와 함께 보여주기만 한다. 사용자가 채팅에 "c17 등록"
이라고 답하면 세션이 이 스크립트를 돌린다. 노션 체크박스도 후보 DB도 쓰지
않는 이유는, 어차피 답을 채팅으로 받기 때문에 노션에 입력 UI를 둘 이유가
없어서다.

진행도 기본값은 출처가 정한다. 스팀 위시리스트는 "사고 싶다"고 이미 찍어둔
목록이라 `구매 대기`가 맞다. 다른 값으로 넣으려면 --진행도를 준다.

사용법:
    python apply_candidates.py 등록 c1 c10
    python apply_candidates.py 버림 c3
    python apply_candidates.py 등록 c7 --진행도 "출시 대기"
    python apply_candidates.py --목록
"""
import argparse
import io
import json
import time
import urllib.request

import candidates
from config import API, headers

IDS_FILE = "db_ids.json"
STAGE_BY_SOURCE = {"스팀 위시리스트": "구매 대기"}
STAGE_DEFAULT = "미확인"


def post(path, payload):
    req = urllib.request.Request(f"{API}{path}", data=json.dumps(payload).encode(),
                                 headers=headers(), method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def props_of(c, stage, today):
    kind = c.get("종류") or "게임"
    field = "진행도(게임)" if kind == "게임" else "진행도(영상)"
    props = {
        "제목": {"title": [{"type": "text", "text": {"content": c["제목"][:1900]}}]},
        "종류": {"select": {"name": kind}},
        field: {"select": {"name": stage}},
        "날짜정밀도": {"select": {"name": "미정"}},
        "변경이력": {"rich_text": [{"type": "text", "text": {
            "content": f"{today}: {c['출처']}에서 후보로 올라와 등록"}}]},
    }
    if c.get("외부ID"):
        props["외부ID"] = {"rich_text": [{"type": "text",
                                        "text": {"content": c["외부ID"]}}]}
    if c.get("레퍼런스"):
        props["레퍼런스"] = {"url": c["레퍼런스"]}
    return props


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("결정", nargs="?", choices=["등록", "버림"])
    ap.add_argument("번호", nargs="*")
    ap.add_argument("--진행도", default=None)
    ap.add_argument("--목록", action="store_true", help="큐 상태만 출력")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    state = candidates.load()

    if a.목록 or not a.결정:
        후보 = sorted(state["후보"].values(),
                    key=lambda c: (c["마지막제시"] or "", c["번호"]))
        보여준 = [c for c in 후보 if c["제시횟수"]]
        print(f"대기 {len(후보)}건 (제시된 것 {len(보여준)}/{candidates.PENDING_MAX})")
        for c in 후보[:15]:
            mark = f"제시 {c['제시횟수']}회" if c["제시횟수"] else "미제시"
            print(f"  {c['번호']:>5} {c['제목'][:38]:38} {c['출처']} · {mark}")
        print(f"처리됨 {len(state['처리됨'])}건")
        return

    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)
    today = time.strftime("%Y-%m-%d")

    for num in a.번호:
        c = state["후보"].get(num)
        if not c:
            print(f"  {num} — 큐에 없다 (이미 처리했거나 없는 번호)")
            continue
        if a.결정 == "등록":
            stage = a.진행도 or STAGE_BY_SOURCE.get(c["출처"], STAGE_DEFAULT)
            print(f"  {num} 등록 → {c['제목']} ({stage})")
            if not a.dry:
                post("/pages", {"parent": {"database_id": ids["work_db"]},
                                "properties": props_of(c, stage, today)})
                time.sleep(0.34)
        else:
            print(f"  {num} 버림 → {c['제목']}")
        if not a.dry:
            candidates.resolve(state, num, a.결정, today)

    if not a.dry:
        candidates.save(state)
        print("큐 갱신 완료")


if __name__ == "__main__":
    main()
