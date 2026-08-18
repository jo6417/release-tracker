# -*- coding: utf-8 -*-
"""작품 DB의 `단계` 값을 스냅샷에서 되돌린다.

2026-08-18, 선택 옵션 이름을 바꾸려고 옵션 목록을 부분만 담아 PATCH 했더니
노션이 목록을 통째로 갈아치웠고, 목록에서 사라진 옵션을 쓰던 행의 값이
API에서 null로 보이게 됐다(676행). 값 자체가 지워진 건 아니어서 옵션을 같은
이름으로 되살리면 돌아온다. **노션 API는 select 옵션 이름 변경을 지원하지 않는다.**
옵션은 추가/삭제만 되고, 이름을 바꾸려면 새 옵션을 만들어 행을 다시 찍어야 한다.

이 스크립트가 하는 일:
    1. 옵션 목록을 새 이름으로 다시 만든다
    2. 스냅샷의 단계 값을 새 이름으로 옮겨 각 행에 다시 넣는다

사용법:
    python restore_stage.py --dry
    python restore_stage.py
"""
import argparse
import io
import json
import sys
import time
import urllib.error
import urllib.request

from config import API, headers

IDS_FILE = "db_ids.json"
SNAP = "snapshots/2026-08-17.json"

OPTIONS = [
    {"name": "기대작", "color": "yellow"},
    {"name": "고민중", "color": "orange"},      # ← 구매판단
    {"name": "보유중", "color": "blue"},        # ← 보유
    {"name": "플레이중", "color": "green"},
    {"name": "완료", "color": "purple"},
    {"name": "미확인", "color": "brown"},
    {"name": "중단", "color": "red"},           # ← 접음
    {"name": "관심없음", "color": "default"},   # ← 거절
]
RENAME = {"구매판단": "고민중", "보유": "보유중", "접음": "중단", "거절": "관심없음"}
# 스냅샷 이후에 생긴 행 (전부 기대작으로 넣었던 것들)
LATE = {"황천의 츠가이": "기대작", "비전 퀘스트": "기대작", "인턴": "기대작"}


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
    raise RuntimeError("재시도 초과")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    import track
    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)
    with io.open(SNAP, encoding="utf-8") as f:
        snap = json.load(f)

    if not a.dry:
        patch(f"/databases/{ids['work_db']}",
              {"properties": {"단계": {"select": {"options": OPTIONS}}}})
        print("옵션 재생성:", " / ".join(o["name"] for o in OPTIONS))

    done, skip, miss = 0, 0, []
    for p in track.query_all(ids["work_db"]):
        title = track.value(p["properties"]["제목"])
        old = (snap.get(p["id"]) or {}).get("단계") or LATE.get(title)
        if not old:
            miss.append(title)
            continue
        new = RENAME.get(old, old)
        if track.value(p["properties"]["단계"]) == new:
            skip += 1
            continue
        if a.dry:
            if done < 5:
                print(f"  {title[:24]:24} → {new}")
        else:
            patch(f"/pages/{p['id']}", {"properties": {"단계": {"select": {"name": new}}}})
            time.sleep(0.34)
        done += 1
        if not a.dry and done % 100 == 0:
            print(f"  {done}행 복구")

    print(f"\n복구 {done}행 / 이미 맞음 {skip}행")
    if miss:
        print(f"근거 없어 못 채운 행 {len(miss)}건: {', '.join(miss[:10])}")
    if a.dry:
        print("--dry: 노션 미변경")


if __name__ == "__main__":
    main()
