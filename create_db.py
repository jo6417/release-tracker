# -*- coding: utf-8 -*-
"""
Notion에 작품 DB / 일정 DB를 생성한다.
한 번만 실행하면 되고, 생성된 DB ID는 db_ids.json에 저장된다.

사용법:
    NOTION_TOKEN=ntn_xxx python3 create_db.py
"""
import json
import os
import sys
import urllib.request
import urllib.error

from config import (API, PARENT_PAGE_ID, WORK_SCHEMA, SCHEDULE_SCHEMA,
                    NOTION_TOKEN, headers)

IDS_FILE = "db_ids.json"


def post(path, payload):
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(payload).encode(),
        headers=headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"[에러] {e.code} {path}\n{body}", file=sys.stderr)
        raise


def patch(path, payload):
    req = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(payload).encode(),
        headers=headers(),
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"[에러] {e.code} {path}\n{e.read().decode()}", file=sys.stderr)
        raise


def create_database(title, icon, schema):
    payload = {
        "parent": {"type": "page_id", "page_id": PARENT_PAGE_ID},
        "icon": {"type": "emoji", "emoji": icon},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": schema,
    }
    res = post("/databases", payload)
    print(f"  생성됨: {title}  ({res['id']})")
    return res["id"]


def main():
    if not NOTION_TOKEN:
        print("NOTION_TOKEN 환경변수가 없습니다.", file=sys.stderr)
        sys.exit(1)

    if os.path.exists(IDS_FILE):
        print(f"{IDS_FILE}이 이미 있습니다. 중복 생성을 막기 위해 중단합니다.")
        print("다시 만들려면 이 파일을 지우고 실행하세요.")
        sys.exit(0)

    print("DB 생성 중...")
    work_id = create_database("작품", "🎯", WORK_SCHEMA)
    sched_id = create_database("일정", "📅", SCHEDULE_SCHEMA)

    # 일정 DB에 작품 관계 추가 (양방향)
    print("관계 속성 연결 중...")
    patch(f"/databases/{sched_id}", {
        "properties": {
            "작품": {
                "relation": {
                    "database_id": work_id,
                    "type": "dual_property",
                    "dual_property": {},
                }
            }
        }
    })
    print("  일정 DB → 작품 DB 관계 생성됨")

    ids = {"work_db": work_id, "schedule_db": sched_id}
    with open(IDS_FILE, "w") as f:
        json.dump(ids, f, indent=2)
    print(f"\n완료. {IDS_FILE}에 저장했습니다.")
    print(json.dumps(ids, indent=2))


if __name__ == "__main__":
    main()
