# -*- coding: utf-8 -*-
"""`진행 단계` 하나를 `진행도(게임)` / `진행도(영상)` 둘로 나눈다.

같은 뼈대에 어휘만 다른 값을 한 속성에 욱여넣으니 영상 쪽이 계속 어색했다.
괄호 병기(`출시(공개) 대기`)도 해봤으나 지저분해서, 매체별 속성으로 나눈다.
어차피 뷰가 매체별로 갈려 있어 한 화면에 둘이 같이 보이지 않는다.

중립인 셋(미확인·일시 중단·폐기됨)은 양쪽 같은 이름을 쓴다. 머릿속에
두 벌을 만들지 않기 위해서다.

이미 값이 들어간 행은 건너뛴다. 중간에 끊겨도 다시 돌리면 이어서 간다.

사용법:
    python split_stage.py --dry
    python split_stage.py
"""
import argparse
import collections
import io
import json
import sys
import time
import urllib.error
import urllib.request

from config import API, headers

IDS_FILE = "db_ids.json"

GAME = [("미확인", "brown"), ("출시 대기", "yellow"), ("구매 대기", "orange"),
        ("보유함", "blue"), ("진행 중", "green"), ("일시 중단", "red"),
        ("폐기됨", "default"), ("완료됨", "purple")]
VIDEO = [("미확인", "brown"), ("공개 대기", "yellow"), ("시청 가능", "blue"),
         ("시청 중", "green"), ("일시 중단", "red"), ("폐기됨", "default"),
         ("시청 완료", "purple")]
# 게임 어휘 → 영상 어휘
V_MAP = {"미확인": "미확인", "출시 대기": "공개 대기", "구매 대기": "공개 대기",
         "보유함": "시청 가능", "진행 중": "시청 중", "일시 중단": "일시 중단",
         "폐기됨": "폐기됨", "완료됨": "시청 완료"}
TARGET = {"게임": "진행도(게임)", "영화": "진행도(영상)", "시리즈": "진행도(영상)"}


def patch(path, body):
    req = urllib.request.Request(f"{API}{path}", data=json.dumps(body).encode(),
                                 headers=headers(), method="PATCH")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt)
                continue
            print(f"[에러] {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
            raise
        except Exception:
            time.sleep(2 ** attempt)
    raise RuntimeError("재시도 초과")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    import track
    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)
    db = ids["work_db"]

    if not a.dry:
        patch(f"/databases/{db}", {"properties": {
            "진행도(게임)": {"select": {"options": [{"name": n, "color": c} for n, c in GAME]}},
            "진행도(영상)": {"select": {"options": [{"name": n, "color": c} for n, c in VIDEO]}}}})
        print("속성 준비 완료")

    n, skip, other = collections.Counter(), 0, []
    for p in track.query_all(db):
        pr = p["properties"]
        kind = track.value(pr["종류"])
        cur = track.value(pr.get("진행 단계") or {"type": "select", "select": None})
        prop = TARGET.get(kind)
        if not cur or not prop:
            if cur:
                other.append(kind)
            continue
        want = V_MAP[cur] if prop == "진행도(영상)" else cur
        if prop in pr and track.value(pr[prop]) == want:
            skip += 1
            continue
        n[(prop, want)] += 1
        if a.dry:
            continue
        patch(f"/pages/{p['id']}", {"properties": {prop: {"select": {"name": want}}}})
        time.sleep(0.34)
        if sum(n.values()) % 100 == 0:
            print(f"  {sum(n.values())}행", flush=True)

    print(f"\n이관 {sum(n.values())}행 / 이미 됨 {skip}행")
    for k, v in sorted(n.items()):
        print(f"  {k[0]} · {k[1]}: {v}")
    if other:
        print("종류가 게임/영화/시리즈가 아닌 행:", collections.Counter(other))
    if a.dry:
        print("--dry: 노션 미변경")


if __name__ == "__main__":
    main()
