# -*- coding: utf-8 -*-
"""config.py의 스키마를 이미 만들어진 Notion DB에 반영한다.

속성 추가와 선택지 변경만 한다. 이름이 같은 속성은 정의를 덮어쓰고,
config에 없는 속성(관계 등)은 건드리지 않는다.

사용법:
    python sync_schema.py            # 작품 DB
    python sync_schema.py --schedule # 일정 DB
"""
import argparse
import io
import json
import sys
import urllib.error
import urllib.request

from config import API, SCHEDULE_SCHEMA, WORK_SCHEMA, headers

IDS_FILE = "db_ids.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schedule", action="store_true", help="일정 DB에 반영")
    a = ap.parse_args()

    with io.open(IDS_FILE, encoding="utf-8") as f:
        ids = json.load(f)
    dbid = ids["schedule_db" if a.schedule else "work_db"]
    schema = SCHEDULE_SCHEMA if a.schedule else WORK_SCHEMA

    req = urllib.request.Request(f"{API}/databases/{dbid}",
                                 data=json.dumps({"properties": schema}).encode(),
                                 headers=headers(), method="PATCH")
    try:
        with urllib.request.urlopen(req) as r:
            db = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"[에러] {e.code}: {e.read().decode()[:400]}", file=sys.stderr)
        sys.exit(1)

    print(f"반영 완료 — 속성 {len(db['properties'])}개")
    for k, v in sorted(db["properties"].items()):
        detail = ""
        if v["type"] in ("select", "multi_select"):
            detail = " / ".join(o["name"] for o in v[v["type"]]["options"])
        print(f"  {k} ({v['type']}) {detail}")


if __name__ == "__main__":
    main()
