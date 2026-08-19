# -*- coding: utf-8 -*-
"""select 옵션 이름을 안전하게 바꾼다.

**노션 API는 select 옵션 이름 변경을 지원하지 않는다.** id와 새 이름을 같이
보내도 조용히 무시된다. 게다가 옵션 목록은 항상 전체를 보내야 하고, 빠진 것은
삭제된다 — 부분만 보내면 나머지가 전부 지워지고 그 값을 쓰던 행이 API에서
빈 값으로 보인다. 2026-08-18과 2026-08-19에 같은 사고를 두 번 냈다
(`restore_stage.py`가 1차 복구본이다).

값 자체가 지워지는 건 아니라 같은 이름으로 옵션을 되살리면 돌아오지만,
애초에 이 순서를 지키면 사고가 나지 않는다:

    1. 새 이름의 옵션을 **추가** (기존 목록 전체 + 새것)
    2. 옛 값을 쓰던 행을 전부 새 값으로 다시 찍는다
    3. 옛 옵션을 **삭제** (목록 전체에서 그것만 뺀 전체를 보낸다)

사용법:
    python rename_option.py --prop "진행도(게임)" --from 클리어 --to 졸업 --dry
    python rename_option.py --prop "진행도(게임)" --from 클리어 --to 졸업
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


def request(path, body=None, method="GET"):
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers(), method=method)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt)
                continue
            print(f"[에러] {e.code} {path}: {e.read().decode()[:300]}", file=sys.stderr)
            raise
    raise RuntimeError("재시도 초과")


def options_of(dbid, prop):
    db = request(f"/databases/{dbid}")
    spec = db["properties"][prop]
    if spec["type"] != "select":
        raise SystemExit(f"{prop}은 select가 아닙니다 ({spec['type']})")
    return [{"name": o["name"], "color": o["color"]} for o in spec["select"]["options"]]


def put_options(dbid, prop, opts):
    """항상 전체 목록을 보낸다. 이 함수를 거치지 않고 부분만 보내면 사고가 난다."""
    request(f"/databases/{dbid}", {"properties": {prop: {"select": {"options": opts}}}},
            method="PATCH")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prop", required=True, help="속성 이름")
    ap.add_argument("--from", dest="old", required=True, help="옛 옵션 이름")
    ap.add_argument("--to", dest="new", required=True, help="새 옵션 이름")
    ap.add_argument("--color", default=None, help="새 옵션 색 (기본: 옛것과 같게)")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    import track
    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)
    dbid = ids["work_db"]

    opts = options_of(dbid, a.prop)
    names = [o["name"] for o in opts]
    if a.old not in names:
        raise SystemExit(f"'{a.old}' 옵션이 없습니다. 현재: {' / '.join(names)}")
    color = a.color or next(o["color"] for o in opts if o["name"] == a.old)

    rows = track.query_all(dbid)
    targets = [p for p in rows
               if track.value(p["properties"][a.prop]) == a.old]
    print(f"{a.prop}: '{a.old}' → '{a.new}'  ({len(targets)}행)")

    if a.dry:
        for p in targets[:10]:
            print("   " + track.value(p["properties"]["제목"]))
        if len(targets) > 10:
            print(f"   … 외 {len(targets) - 10}건")
        print("--dry: 노션 미변경")
        return

    # 1) 새 옵션 추가 (전체 목록 + 새것)
    if a.new not in names:
        put_options(dbid, a.prop, opts + [{"name": a.new, "color": color}])
        print(f"  옵션 추가: {a.new}")

    # 2) 행 이관
    for n, p in enumerate(targets, 1):
        request(f"/pages/{p['id']}",
                {"properties": {a.prop: {"select": {"name": a.new}}}}, method="PATCH")
        time.sleep(0.34)
        if n % 50 == 0:
            print(f"  {n}/{len(targets)}행")
    print(f"  {len(targets)}행 이관 완료")

    # 3) 옛 옵션 삭제 (그것만 뺀 전체를 보낸다)
    left = [o for o in options_of(dbid, a.prop) if o["name"] != a.old]
    put_options(dbid, a.prop, left)
    print(f"  옵션 삭제: {a.old}")
    print("\n최종:", " / ".join(o["name"] for o in options_of(dbid, a.prop)))


if __name__ == "__main__":
    main()
