# -*- coding: utf-8 -*-
"""출시 트래커 공통 설정"""
import os

_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def _load_env():
    """.env가 있으면 환경변수로 읽어들인다 (이미 설정된 값이 우선)."""
    if not os.path.exists(_ENV_FILE):
        return
    with open(_ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'").strip('"'))


_load_env()

NOTION_TOKEN = os.environ.get("NOTION_TOKEN", "")
NOTION_VERSION = "2022-06-28"
PARENT_PAGE_ID = "3be857fe-7a80-808e-b6ad-c595ff14a3e8"

API = "https://api.notion.com/v1"


def headers():
    return {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


# ─────────────────────────────────────────────
# 작품 DB 스키마
# ─────────────────────────────────────────────
WORK_SCHEMA = {
    "제목": {"title": {}},
    "원제": {"rich_text": {}},
    "종류": {"select": {"options": [
        {"name": "게임", "color": "blue"},
        {"name": "영화", "color": "purple"},
        {"name": "시리즈", "color": "pink"},
        {"name": "만화", "color": "brown"},
    ]}},
    # 매체와 직교하는 축. 애니 극장판=영화+체크, 애니 시리즈=시리즈+체크,
    # 실사화=체크 해제. 종류 조합을 늘리지 않고 담기 위한 것.
    "애니메이션": {"checkbox": {}},
    "외부ID": {"rich_text": {}},

    "단계": {"select": {"options": [
        {"name": "추천됨", "color": "gray"},
        {"name": "기대작", "color": "yellow"},
        {"name": "구매판단", "color": "orange"},
        {"name": "보유", "color": "blue"},
        {"name": "플레이중", "color": "green"},
        {"name": "완료", "color": "purple"},
        {"name": "미확인", "color": "brown"},
        {"name": "접음", "color": "red"},
        {"name": "거절", "color": "default"},
    ]}},

    "날짜정밀도": {"select": {"options": [
        {"name": "미정", "color": "red"},
        {"name": "연도", "color": "orange"},
        {"name": "분기", "color": "yellow"},
        {"name": "월", "color": "blue"},
        {"name": "확정", "color": "green"},
    ]}},
    "출시·개봉일": {"date": {}},
    "추정시기": {"rich_text": {}},
    "국내 출시·개봉일": {"date": {}},

    "플랫폼": {"multi_select": {"options": [
        {"name": "PC", "color": "gray"},
        {"name": "PS5", "color": "blue"},
        {"name": "PS4", "color": "blue"},
        {"name": "PS3", "color": "blue"},
        {"name": "Switch", "color": "red"},
        {"name": "Switch2", "color": "red"},
        {"name": "Xbox", "color": "green"},
    ]}},
    "공개처": {"multi_select": {"options": [
        {"name": "극장", "color": "purple"},
        {"name": "넷플릭스", "color": "red"},
        {"name": "디즈니+", "color": "blue"},
        {"name": "왓챠", "color": "pink"},
        {"name": "티빙", "color": "orange"},
        {"name": "웨이브", "color": "blue"},
        {"name": "쿠팡플레이", "color": "red"},
        {"name": "애플TV+", "color": "gray"},
        {"name": "프라임비디오", "color": "blue"},
    ]}},
    "한국어지원": {"select": {"options": [
        {"name": "자막", "color": "green"},
        {"name": "더빙", "color": "blue"},
        {"name": "미지원", "color": "red"},
        {"name": "미정", "color": "gray"},
    ]}},

    "소유처": {"multi_select": {"options": [
        {"name": "스팀", "color": "blue"},
        {"name": "에픽", "color": "gray"},
        {"name": "GOG", "color": "purple"},
        {"name": "PS스토어", "color": "blue"},
        {"name": "닌텐도", "color": "red"},
        {"name": "게임패스", "color": "green"},
        {"name": "PS플러스", "color": "yellow"},
        {"name": "실물", "color": "brown"},
    ]}},
    "획득경로": {"multi_select": {"options": [
        {"name": "구매", "color": "blue"},
        {"name": "무료배포", "color": "green"},
        {"name": "구독포함", "color": "yellow"},
        {"name": "번들", "color": "orange"},
    ]}},
    "소유상세": {"rich_text": {}},
    "중복소유": {"checkbox": {}},
    "게임패스": {"checkbox": {}},

    "정가": {"number": {"format": "won"}},
    "현재최저가": {"number": {"format": "won"}},
    "역대최저가": {"number": {"format": "won"}},
    "목표할인율": {"number": {"format": "percent"}},
    "평점": {"number": {"format": "number"}},

    "클리어일": {"date": {}},
    "마지막플레이일": {"date": {}},
    "플레이시간": {"number": {"format": "number"}},
    "개인평점": {"select": {"options": [
        {"name": "★5", "color": "green"},
        {"name": "★4", "color": "blue"},
        {"name": "★3", "color": "yellow"},
        {"name": "★2", "color": "orange"},
        {"name": "★1", "color": "red"},
    ]}},
    "한줄평": {"rich_text": {}},
    "후속작대기": {"checkbox": {}},

    "마지막확인": {"date": {}},
    "변경이력": {"rich_text": {}},
    "레퍼런스": {"url": {}},
    "스티커원본": {"rich_text": {}},
}

# ─────────────────────────────────────────────
# 일정 DB 스키마 (작품 관계는 생성 후 추가)
# ─────────────────────────────────────────────
SCHEDULE_SCHEMA = {
    "이름": {"title": {}},
    "종류": {"select": {"options": [
        # 게임
        {"name": "발표", "color": "gray"},
        {"name": "예약구매", "color": "yellow"},
        {"name": "알파테스트", "color": "orange"},
        {"name": "베타테스트", "color": "orange"},
        {"name": "데모", "color": "orange"},
        {"name": "얼리액세스", "color": "brown"},
        {"name": "정식출시", "color": "green"},
        {"name": "DLC", "color": "purple"},
        {"name": "무료배포", "color": "pink"},
        # 영상
        {"name": "극장개봉", "color": "green"},
        {"name": "국내개봉", "color": "green"},
        {"name": "OTT공개", "color": "blue"},
        {"name": "시즌시작", "color": "blue"},
        {"name": "파트공개", "color": "blue"},
        {"name": "최종화", "color": "red"},
        # 만화
        {"name": "회차", "color": "brown"},
    ]}},
    "날짜": {"date": {}},
    "날짜정밀도": {"select": {"options": [
        {"name": "미정", "color": "red"},
        {"name": "연도", "color": "orange"},
        {"name": "분기", "color": "yellow"},
        {"name": "월", "color": "blue"},
        {"name": "확정", "color": "green"},
    ]}},
    "시즌번호": {"number": {"format": "number"}},
    "회차": {"number": {"format": "number"}},
    "공개방식": {"select": {"options": [
        {"name": "일괄공개", "color": "blue"},
        {"name": "주간공개", "color": "green"},
        {"name": "극장", "color": "purple"},
        {"name": "해당없음", "color": "gray"},
    ]}},
    "진행도": {"rich_text": {}},
    "캘린더노출": {"checkbox": {}},
}

# 캘린더 기본 노출 대상
CALENDAR_VISIBLE = {"정식출시", "극장개봉", "국내개봉", "OTT공개", "시즌시작"}
