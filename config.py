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

    # 작품이 아니라 **나**의 진행 상태. 작품 쪽 상태(나왔나·완결됐나)는
    # 이용 가능일·날짜정밀도·방영상태가 따로 갖고 있다.
    #
    # 게임과 영상으로 속성을 나눴다. 같은 뼈대에 어휘만 다른 값을 한 속성에
    # 넣으니 영상 쪽이 계속 어색했고, 괄호 병기는 지저분했다. 뷰가 매체별로
    # 갈려 있어 한 화면에 둘이 같이 보이지 않는다.
    # 중립인 셋(미확인·일시 중단·폐기됨)만 양쪽 같은 이름을 쓴다.
    "진행도(게임)": {"select": {"options": [
        {"name": "미확인", "color": "brown"},
        {"name": "출시 대기", "color": "yellow"},
        {"name": "구매 대기", "color": "orange"},      # 살 예정
        {"name": "보유함", "color": "blue"},
        # 끝이 없는 게임 — 온라인·캐주얼·샌드박스처럼 엔딩으로 갈 일이 없는 것.
        # `보유함`에 섞어두면 "아직 안 한 것"으로 잡혀 백로그 알림에 영원히 뜬다.
        # 안 한 게 아니라 끝이 없는 것이라, 알림 대상에서 통째로 뺀다.
        # (Backloggery의 `Endless`, Backloggd의 `Retired`와 같은 자리다)
        {"name": "엔딩 없음", "color": "blue"},
        {"name": "진행 중", "color": "green"},
        {"name": "일시 중단", "color": "red"},
        {"name": "구매 보류", "color": "default"},     # 안 사기로 한 것
        {"name": "폐기됨(노잼)", "color": "gray"},
        # 엔딩을 본 것과 "더 파먹을 게 없어 그만둔 것"을 한 값으로 묶는다.
        # 엔딩 없는 게임에는 클리어가 없지만 졸업은 있다.
        {"name": "졸업", "color": "purple"},
    ]}},
    "진행도(영상)": {"select": {"options": [
        {"name": "미확인", "color": "brown"},
        {"name": "공개 대기", "color": "yellow"},
        {"name": "시청 안함", "color": "blue"},        # 아직 안 본 것 (= 볼 대상)
        {"name": "시청 중", "color": "green"},
        {"name": "시청 일시 중단", "color": "red"},
        {"name": "시청 보류", "color": "gray"},
        {"name": "폐기됨(노잼)", "color": "default"},
        {"name": "시청 완료", "color": "purple"},
    ]}},

    "날짜정밀도": {"select": {"options": [
        {"name": "미정", "color": "red"},
        {"name": "연도", "color": "orange"},
        {"name": "분기", "color": "yellow"},
        {"name": "월", "color": "blue"},
        {"name": "확정", "color": "green"},
        # 방영은 시작했지만 완결되면 몰아보려고 기다리는 중.
        # 진행도는 출시 대기(공개 대기) 그대로 두고 여기에만 표시한다.
        # 확정이 아니므로 작품 자체는 캘린더에 안 뜨고, 완결일만 최종화 일정으로 뜬다.
        {"name": "완결대기", "color": "pink"},
    ]}},
    # 국내/해외를 나누지 않는다. 아는 날짜를 넣어두고 국내 날짜가 나오면 덮어쓴다.
    # (덮어쓰면 변경이력에 남고 track.py가 [날짜변경]으로 알려준다)
    "출시·개봉일": {"date": {}},

    # 정렬용 단일 기준. "내가 이걸 볼/할 수 있게 되는 날"이다.
    # 출시일과 완결일이 갈려 있으면 뷰를 어느 쪽으로 정렬해도 한쪽이 어긋나서,
    # 원본 두 날짜는 그대로 두고 표시용 날짜만 하나 뽑는다.
    "이용 가능일": {"formula": {"expression":
        'if(prop("날짜정밀도") == "완결대기" and not empty(prop("완결일")), '
        'prop("완결일"), prop("출시·개봉일"))'}},

    # ── 시리즈 완결 추적 ──
    # "완결되면 몰아보려고 대기 중"인 작품을 매번 손으로 확인하지 않기 위한 것.
    # 대기 표시는 날짜정밀도 = 완결대기로 한다 (별도 속성을 두지 않는다).
    # 아래 셋은 sync_series.py가 TMDB로 채운다.
    "방영상태": {"select": {"options": [
        {"name": "방영예정", "color": "gray"},
        {"name": "방영중", "color": "green"},
        {"name": "시즌완결", "color": "blue"},     # 이번 시즌 끝, 다음 시즌 있음
        {"name": "완결", "color": "purple"},       # 시리즈 자체가 종영
        {"name": "취소", "color": "red"},
        {"name": "휴방", "color": "orange"},       # 다음 화 미정인 채로 멈춤
    ]}},
    "방영진행": {"rich_text": {}},                 # "7/12화 · 다음 2026-08-22"
    # 방영이 끝났으면 실제 마지막 화 날짜, 방영 중이면 추정치.
    # 어느 쪽인지는 방영상태·방영진행이 말해준다.
    "완결일": {"date": {}},

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
    "게임패스": {"checkbox": {}},

    "정가": {"number": {"format": "won"}},
    "현재최저가": {"number": {"format": "won"}},
    "역대최저가": {"number": {"format": "won"}},
    "평점": {"number": {"format": "number"}},

    "졸업일": {"date": {}},
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
