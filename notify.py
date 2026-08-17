# -*- coding: utf-8 -*-
"""알림 발송 — 노션 멘션.

댓글 권한이 있으면 댓글로, 없으면 알림 페이지 본문에 줄을 추가한다.
어느 쪽이든 멘션이라 노션이 푸시를 보내준다.
"""
import io
import json
import os
import urllib.error
import urllib.request

from config import API, headers

IDS_FILE = "notify_ids.json"


def _ids():
    with io.open(IDS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _rich(user_id, text):
    return [
        {"type": "mention", "mention": {"user": {"id": user_id}}},
        {"type": "text", "text": {"content": " " + text[:1900]}},
    ]


def send(text):
    """멘션 알림 1건. 성공하면 True."""
    ids = _ids()
    rich = _rich(ids["user_id"], text)

    # 1) 댓글 (권한이 있으면 이쪽이 깔끔하다)
    req = urllib.request.Request(
        f"{API}/comments",
        data=json.dumps({"parent": {"page_id": ids["notify_page"]},
                         "rich_text": rich}).encode(),
        headers=headers(), method="POST")
    try:
        urllib.request.urlopen(req).read()
        return True
    except urllib.error.HTTPError as e:
        if e.code != 403:
            raise

    # 2) 본문에 줄 추가 (댓글 권한이 없을 때)
    body = {"children": [{"object": "block", "type": "paragraph",
                          "paragraph": {"rich_text": rich}}]}
    req = urllib.request.Request(f"{API}/blocks/{ids['notify_page']}/children",
                                 data=json.dumps(body).encode(),
                                 headers=headers(), method="PATCH")
    urllib.request.urlopen(req).read()
    return True
