"""Send a finished Notion briefing through FCM HTTP v1."""

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit


NOTION_HOSTS = {"app.notion.com", "www.notion.com", "notion.so", "www.notion.so"}


class PushRejected(Exception):
    """FCM or the sender rejected the message before delivery."""


@dataclass(frozen=True)
class PushTarget:
    project: str
    token: str
    access_token: str

    @classmethod
    def from_environment(cls):
        project = os.environ.get("KKACHI_FCM_PROJECT", "").strip()
        token = os.environ.get("KKACHI_FCM_TOKEN", "").strip()
        access = os.environ.get("KKACHI_FCM_ACCESS_TOKEN", "").strip()
        if re.fullmatch("[a-z][a-z0-9-]{4,28}[a-z0-9]", project) and token and access:
            return cls(project, token, access)
        return None

    def send(self, page_id, title, summary, url):
        data = briefing_data(page_id, title, summary, url)
        payload = {
            "message": {
                "token": self.token,
                "data": data,
                "android": {"priority": "high", "ttl": "3600s"},
            }
        }
        req = urllib.request.Request(
            f"https://fcm.googleapis.com/v1/projects/{self.project}/messages:send",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                receipt = json.load(response)
        except urllib.error.HTTPError as error:
            if 400 <= error.code < 500 and error.code != 408:
                raise PushRejected(f"FCM이 발송을 거부했습니다 (HTTP {error.code})") from None
            raise RuntimeError(f"FCM 응답을 확인할 수 없습니다 (HTTP {error.code})") from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise RuntimeError("FCM 발송 결과를 확인할 수 없습니다.") from None
        if not isinstance(receipt, dict) or not receipt.get("name"):
            raise RuntimeError("FCM 발송 확인값을 받지 못했습니다.")


def briefing_data(page_id, title, summary, url):
    try:
        page_id = uuid.UUID(page_id).hex
        parsed = urlsplit(url)
        port = parsed.port
    except (ValueError, TypeError, AttributeError):
        raise PushRejected("브리핑 원문 링크를 확인할 수 없습니다.") from None
    if (parsed.scheme != "https" or parsed.hostname not in NOTION_HOSTS or port not in (None, 443)
            or parsed.username is not None or len(parsed.path) <= 1 or len(url.encode("utf-8")) > 900):
        raise PushRejected("브리핑 원문 링크를 확인할 수 없습니다.")
    return {
        "kind": "works-briefing",
        "id": page_id,
        "title": title[:180],
        "summary": "\n".join(line.strip() for line in summary if line.strip()).encode("utf-8")[:2200].decode("utf-8", "ignore"),
        "url": url,
        "at": str(int(time.time() * 1000)),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="까치 우편 실푸시 한 건 시험")
    parser.add_argument("--test-url", required=True)
    args = parser.parse_args()
    target = PushTarget.from_environment()
    if target is None:
        parser.error("까치 우편 Firebase 발송 설정을 입력하세요.")
    try:
        target.send(str(uuid.uuid4()), "까치 우편 직접 푸시 시험",
                    ["갤럭시 알림창에 까치 알림이 표시되는지 확인",
                     "알림에서 이 요약과 노션 원문 링크를 확인"],
                    args.test_url)
    except (PushRejected, RuntimeError) as error:
        parser.exit(1, f"{error}\n")
    print("까치 우편 시험 푸시 접수 완료")
