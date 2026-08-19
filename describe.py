# -*- coding: utf-8 -*-
"""알림 문구 — 한 줄 요약과 여러 줄 상세를 만든다.

알림에 제목만 뜨면 "이게 뭐였지"로 끝난다. 언제부터 볼 수 있고 무엇을 하면
되는지까지 적어야 알림이 행동으로 이어진다. 이 모듈은 작품 한 행(스냅샷 dict)을
받아 그 문장을 만든다.

track.py / sync_prices.py가 공유한다.
"""
import datetime

# 진행도는 매체별로 속성이 갈려 있다. 한 행은 둘 중 하나만 쓴다.
STAGE = ("진행도(게임)", "진행도(영상)")


def _date(iso):
    if not iso:
        return None
    try:
        return datetime.date.fromisoformat(iso[:10])
    except (ValueError, TypeError):
        return None


def dday(iso, today=None):
    """'D-12' / '오늘' / '12일 전'. 날짜만 적으면 멀고 가까움이 안 읽힌다."""
    d = _date(iso)
    if not d:
        return ""
    today = today or datetime.date.today()
    n = (d - today).days
    if n == 0:
        return "오늘"
    return f"D-{n}" if n > 0 else f"{-n}일 전"


def stage_of(row):
    """이 행이 실제로 쓰는 진행도 값. 게임이든 영상이든 하나만 차 있다."""
    for k in STAGE:
        if row.get(k):
            return row[k]
    return None


def _join(v):
    return "/".join(v) if isinstance(v, list) else v


def euro(word):
    """받침에 맞는 '으로/로'. '게임패스으로' 같은 게 알림에 그대로 나갔었다."""
    ch = word[-1] if word else ""
    if not ("가" <= ch <= "힣"):
        return "로"
    jong = (ord(ch) - 0xAC00) % 28
    return "로" if jong in (0, 8) else "으로"      # 받침 없음 또는 ㄹ


def kind_of(row):
    """작품의 매체(게임·영화·시리즈·만화). 알림 종류와 헷갈리지 않게 따로 둔다."""
    return row.get("종류")


def head_line(row):
    """'시리즈 · 쿠팡플레이 · 방영중' — 이게 뭐였는지 떠올리게 하는 한 줄."""
    bits = [row.get("종류") or "작품"]
    for k in ("공개처", "플랫폼", "소유처"):
        if row.get(k):
            bits.append(_join(row[k]))
            break
    if row.get("방영상태"):
        bits.append(row["방영상태"])
    stage = stage_of(row)
    if stage:
        bits.append(stage)
    return " · ".join(str(b) for b in bits)


def date_line(row, today=None):
    """날짜 한 줄. 방영진행이 있으면 그쪽이 화수·다음화·완결예정을 다 갖고 있다."""
    parts = []
    rel = row.get("출시·개봉일")
    if rel:
        verb = {"게임": "출시", "영화": "개봉", "만화": "연재"}.get(row.get("종류"), "공개")
        prec = row.get("날짜정밀도")
        if prec in ("미정", "연도", "분기", "월"):
            parts.append(f"{rel[:10]} {verb} ({prec} 단위 추정)")
        else:
            parts.append(f"{rel[:10]} {verb} ({dday(rel, today)})")
    if row.get("방영진행"):
        parts.append(row["방영진행"])
    elif row.get("완결일"):
        fin = row["완결일"]
        parts.append(f"{fin[:10]} 완결 ({dday(fin, today)})")
    return " · ".join(parts)


def watch_line(row, today=None, has_date_line=False):
    """언제부터 보면 되는지. 알림을 행동으로 바꾸는 줄이라 웬만하면 넣는다.

    `has_date_line`이 True면 바로 윗줄이 이미 날짜와 D-day를 말한 상태다.
    그때 "그 날짜부터 볼 수 있음"을 또 적으면 같은 숫자가 두 번 나온다.
    덧붙일 게 없으면 None을 돌려 줄을 통째로 뺀다.
    """
    today = today or datetime.date.today()
    verb = "할 수 있음" if kind_of(row) == "게임" else "볼 수 있음"
    # 완결/시즌완결이면 기다림은 끝난 것이다. 이걸 안 보고 날짜정밀도만 보면
    # 완결 알림에 "완결 기다리는 중"이 붙는다.
    done = row.get("방영상태") in ("완결", "시즌완결")

    if row.get("날짜정밀도") == "완결대기" and not done:
        fin = row.get("완결일")
        if fin:
            return f"→ 완결 기다리는 중. {fin[:10]}({dday(fin, today)})부터 정주행"
        return "→ 완결 기다리는 중 (완결일 아직 미정)"

    av = row.get("이용 가능일")
    d = _date(av)
    if not d:
        return "→ 날짜 미정. 확정되면 다시 알림"
    if d > today:
        if has_date_line and av == row.get("출시·개봉일"):
            return None          # 윗줄이 이미 같은 날짜와 D-day를 말했다
        return f"→ {av[:10]}부터 {verb} ({dday(av, today)})"
    if done:
        return "→ 완결. 이제 몰아볼 수 있음"      # 화수·종영일은 윗줄이 이미 말했다
    if row.get("방영상태") == "방영중":
        return "→ 지금 주간 방영 중. 몰아볼 거면 완결까지 대기"
    return f"→ 이미 {verb} (공개 {dday(av, today)})"


# ─────────────────────────────────────────────
# 종류별 상세
# ─────────────────────────────────────────────
def detail(kind, row, note=None, today=None):
    """알림 한 건의 상세 줄들. 첫 줄은 신원, 가운데는 날짜, 끝은 할 일."""
    if kind == "방치":
        lines = [head_line(row)]
        if note:
            lines.append(note)
        hrs = row.get("플레이시간")
        if hrs:
            lines.append(f"누적 {hrs}시간")
        lines.append("→ 이어서 하거나, 진행도를 '일시 중단'으로 옮기면 이 알림이 멈춘다")
        return lines

    if kind == "할인":
        return [head_line(row)] + ([note] if note else [])

    if kind == "백로그":
        # 이미 나왔는데 손 안 댄 것. 같은 숫자를 세 번 반복하지 않는다.
        lines = [head_line(row)]
        av = row.get("이용 가능일")
        d = _date(av)
        if d:
            days = ((today or datetime.date.today()) - d).days
            verb = {"게임": "출시", "영화": "개봉"}.get(kind_of(row), "공개")
            lines.append(f"{av[:10]} {verb} — 나온 지 {days}일째 그대로")
        undone = "안 함" if kind_of(row) == "게임" else "안 봄"
        lines.append(f"→ 아직 {undone}. 할 거면 지금, 아니면 진행도를 "
                     "'보류'나 '폐기됨'으로")
        return lines

    if kind == "진행도":
        # 진행도가 바뀐 날 출시일이나 "이미 할 수 있음"은 아무 쓸모가 없다.
        lines = [head_line(row)]
        if note:
            lines.append(note)
        last = row.get("마지막플레이일")
        if last:
            lines.append(f"마지막 {last[:10]} ({dday(last, today)})"
                         + (f" · 누적 {row['플레이시간']}시간" if row.get("플레이시간") else ""))
        return lines

    if kind == "게임패스":
        # 이 알림의 핵심은 "구독으로 지금 공짜로 할 수 있다"는 것 하나다.
        # 게임패스 자체는 소유처에서 빼고 본다 — 입점 알림에 "이미 게임패스로
        # 갖고 있음"이라고 답하면 아무 말도 안 한 것이 된다.
        lines = [head_line(row)]
        owned = [o for o in (row.get("소유처") or []) if o != "게임패스"]
        if owned:
            j = _join(owned)
            lines.append(f"→ 이미 {j}{euro(j)} 갖고 있음. 중복 구매 주의")
        else:
            lines.append("→ 게임패스에 들어옴. 구독 중이면 지금 바로 할 수 있다")
        return lines

    if kind == "볼차례":
        # note가 "오늘 공개. 이제 볼 수 있다"라 볼 시점 줄을 또 붙이면 겹친다.
        lines = [head_line(row)]
        if note:
            lines.append(note)
        if row.get("방영진행"):
            lines.append(row["방영진행"])
        return lines

    if kind == "진행중":
        lines = [head_line(row)]
        last = row.get("마지막플레이일")
        bits = []
        if last:
            bits.append(f"마지막 {last[:10]} ({dday(last, today)})")
        if row.get("플레이시간"):
            bits.append(f"누적 {row['플레이시간']}시간")
        if row.get("방영진행"):
            bits.append(row["방영진행"])
        if bits:
            lines.append(" · ".join(bits))
        if note:
            lines.append(note)
        return lines

    lines = [head_line(row)]
    dl = date_line(row, today)
    if dl:
        lines.append(dl)
    if note:
        lines.append(note)
    wl = watch_line(row, today, has_date_line=bool(dl))
    if wl:
        lines.append(wl)
    return lines
