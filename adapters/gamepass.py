# -*- coding: utf-8 -*-
"""엑스박스 게임패스 어댑터 — 카탈로그 목록과 이탈 예고.

공식 문서가 있는 API는 없다. 대신 Xbox 웹사이트가 스스로 쓰는 JSON 엔드포인트
두 개와, 공식 블로그의 태그 피드를 쓴다. 2026-08-22에 셋 다 실측했다.

  sigls          컬렉션에 든 상품 ID 목록. 콘솔 558건 / PC 472건
  displaycatalog 상품 ID -> 제목·개발사·소개·출시일. 요청당 20개 남짓씩 끊는다
  태그 피드       'Coming to XBOX Game Pass' 글에 입점 예정일과 이탈 목록이 있다

문서화된 API가 아니라 예고 없이 바뀔 수 있다. 그래서 카탈로그가 비거나 절반으로
줄면 healthcheck가 경보를 낸다 - 조용히 멈추는 것이 제일 나쁘다.

전체 피드(news.xbox.com/en-us/feed/)를 쓰면 안 된다. 11건만 담고 있어서
게임패스 글이 하루 이틀이면 밀려난다. 태그 피드를 써야 한다.
"""
import io
import json
import re
import urllib.request

SIGLS = "https://catalog.gamepass.com/sigls/v2"
CATALOG = "https://displaycatalog.mp.microsoft.com/v7.0/products"
FEED = "https://news.xbox.com/en-us/tag/xbox-game-pass/feed/"

# 컬렉션 GUID. '모든 콘솔 게임'과 '모든 PC 게임'이다.
COLLECTIONS = {
    "콘솔": "f6f1f99f-9b49-4ccd-b3bf-4d9767a77f5e",
    "PC": "fdd9e2a7-0fee-49f6-ad69-4354098401ff",
}
MARKET = "KR"
LANG = "ko-kr"
BATCH = 20


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "release-tracker"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def catalog_ids():
    """게임패스에 들어 있는 상품 ID 전부. 콘솔과 PC를 합친다."""
    out = set()
    for name, guid in COLLECTIONS.items():
        url = f"{SIGLS}?id={guid}&language={LANG}&market={MARKET}"
        rows = json.loads(_get(url))
        ids = {r["id"] for r in rows if "id" in r}
        if not ids:
            raise RuntimeError(f"{name} 컬렉션이 비어 있다 - 엔드포인트가 바뀌었을 수 있다")
        out |= ids
    return out


def titles(product_ids, lang=LANG):
    """상품 ID -> 제목. 요청당 BATCH개씩 끊어 묻는다.

    한국어와 영어를 둘 다 받아둔다. 이탈 예고는 Xbox Wire에서 오는데 그쪽은
    영문이고, 카탈로그를 ko-kr로만 받으면 대조할 방법이 없다. 노션에도 원제가
    채워져 있어 영문 쪽이 오히려 더 잘 붙는다.
    """
    out = {}
    ids = list(product_ids)
    for i in range(0, len(ids), BATCH):
        chunk = ",".join(ids[i:i + BATCH])
        url = (f"{CATALOG}?bigIds={chunk}&market={MARKET}&languages={lang}"
               f"&MS-CV=release-tracker")
        try:
            d = json.loads(_get(url))
        except Exception:
            continue                      # 한 묶음이 죽어도 나머지는 받는다
        for p in d.get("Products", []):
            loc = (p.get("LocalizedProperties") or [{}])[0]
            name = (loc.get("ProductTitle") or "").strip()
            if name:
                out[p["ProductId"]] = name
    return out


def info(product_ids, lang=LANG):
    """상품 ID -> {제목, 개발사, 소개, 출시일}. 미등록 입점작의 상세에 쓴다.

    `titles()`와 같은 엔드포인트지만 카탈로그가 주는 나머지 칸까지 받는다.
    카탈로그에 이름만 있으면 "이게 뭔 게임이지"로 끝나서 알림이 무용지물이다.
    개발사는 ko-kr 응답에서 비어 오는 상품이 있어 배급사로 대신한다.
    """
    out = {}
    ids = list(product_ids)
    for i in range(0, len(ids), BATCH):
        chunk = ",".join(ids[i:i + BATCH])
        url = (f"{CATALOG}?bigIds={chunk}&market={MARKET}&languages={lang}"
               f"&MS-CV=release-tracker")
        try:
            d = json.loads(_get(url))
        except Exception:
            continue                      # 한 묶음이 죽어도 나머지는 받는다
        for p in d.get("Products", []):
            loc = (p.get("LocalizedProperties") or [{}])[0]
            name = (loc.get("ProductTitle") or "").strip()
            if not name:
                continue
            mk = (p.get("MarketProperties") or [{}])[0]
            out[p["ProductId"]] = {
                "제목": name,
                "개발사": (loc.get("DeveloperName")
                          or loc.get("PublisherName") or "").strip(),
                "소개": (loc.get("ShortDescription")
                        or loc.get("ProductDescription") or "").strip(),
                "출시일": (mk.get("OriginalReleaseDate") or "")[:10],
            }
    return out


def _clean(html):
    return re.sub(r"<[^>]+>|\]\]>", "", html).replace("&#8217;", "'").strip()


def leaving():
    """곧 내려가는 작품. [(제목, 'YYYY-MM-DD' 또는 원문 날짜), ...]

    공식 발표라 신뢰도가 높지만 제목이 영문이고 플랫폼 표기가 붙어 있어
    (Cloud, Console, and PC) 같은 꼬리를 떼야 한다.
    """
    xml = _get(FEED).decode("utf-8", "replace")
    items = re.findall(r"<item>(.*?)</item>", xml, re.S)
    out = []
    for it in items:
        title = _clean(re.search(r"<title>(.*?)</title>", it, re.S).group(1))
        if "Coming to XBOX Game Pass" not in title:
            continue
        body = re.search(r"<content:encoded>(.*?)</content:encoded>", it, re.S)
        if not body:
            continue
        parts = re.split(r"<h[23][^>]*>(.*?)</h[23]>", body.group(1), flags=re.S)
        for i in range(1, len(parts), 2):
            head = _clean(parts[i])
            m = re.match(r"Leaving\s+(.+)", head)
            if not m:
                continue
            when = m.group(1).strip()
            for li in re.findall(r"<li[^>]*>(.*?)</li>", parts[i + 1], re.S):
                name = re.sub(r"\s*\((?:Cloud|Console|PC|XBOX|Handheld)[^)]*\)\s*$",
                              "", _clean(li)).strip()
                if name:
                    out.append((name, when))
        break                             # 가장 최근 글 하나면 충분하다
    return out
