"""일일 투자 브리핑의 DB·LLM 계층(2026-11-06): 입력 읽기(읽기 전용) → 요약(LLM, 실패하면 코드) → 저장(불변 스냅샷)·조회.

입력은 모두 저장된 DB 값이다(외부 시세·뉴스·공시 API를 부르지 않는다). 기존 함수를 재사용한다:
- 보유·관심·시세·뉴스·공시·지수·환율: app.briefing_db.load_inputs + app.briefing.build_briefing(GET /api/briefing과 같은 계산)
- 시세 수집 상태(기대 거래일 시세가 없을 때 이유): app.outlook.price_status, 마지막 갱신 실행: app.data_status.summarize_last_run
- 업종·기초지수 간접 뉴스: app.related_news.indirect_news, ETF 실질 노출: app.exposure_db.get_exposure
- 숫자 허용 목록·금지 표현·모르는 종목명: app.diagnosis(allowed_numbers, find_disallowed_numbers, FORBIDDEN_PHRASES, find_unknown_companies)
diagnosis.load_inputs는 실시간 시세(외부)를 불러서 쓰지 않는다. 위험 점검은 브리핑과 같은 DB 종가로 compute_portfolio를 돌린다.

저장 정책: 같은 사용자·같은 날짜(사용자 시간대 기준)는 version 1 한 건. regenerate=true일 때만 version을 올린 새 행을 더한다(하루
MAX_VERSIONS개까지). 기존 행은 고치지 않는다. 조회는 저장한 content를 그대로 돌려주고 다시 계산하지 않는다.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import ollama
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import daily_briefing as dbf
from app import related_news
from app.briefing import KST, RECENT_DAYS, build_briefing, drop_open_session, session_is_open
from app.briefing_db import load_inputs
from app.data_status import REFRESH_STEPS, MAX_STEPS_PER_RUN, summarize_last_run
from app.diagnosis import FORBIDDEN_PHRASES, allowed_numbers, find_disallowed_numbers, find_unknown_companies
from app.exposure_db import get_exposure
from app.history_dates import date_fields
from app.llm_settings import call_options, chat_model
from app.models import Company, DailyBriefing, DataRefreshLog, Disclosure, ExchangeRate, MarketIndex, News, ResearchReport
from app.outlook import price_status
from app.tools.fx_tool import PAIR_CODE

logger = logging.getLogger(__name__)

NEWS_FETCH = 10  # 종목당 읽는 최근 뉴스(비슷한 제목을 묶은 뒤 dbf.NEWS_SHOWN개만 보인다)
SERIES_ROWS = 8  # 지수·환율 최근 행(5거래일 변화 + 장중 행 여유)
MARKET_NEWS_DAYS = 3
MARKET_NEWS_FETCH = 30
MARKET_NEWS_RE = r"코스피|코스닥|증시|환율|원\s*/?\s*달러|기준\s*금리|연준|FOMC|외국인\s*순(?:매수|매도)"
MARKET_INDEX_RE = re.compile(r"코스피|코스닥")  # 지수 이름이 제목에 있는 기사를 먼저 보인다
MAX_VERSIONS = 5  # 하루에 만들 수 있는 브리핑 수(첫 생성 + 다시 만들기 4번)
LLM_TIMEOUT_SECONDS = 60
LLM_MAX_ATTEMPTS = 2
SUMMARY_MAX_CHARS = 600
_EXTRA_FORBIDDEN = re.compile(r"목표\s*주가|목표가|확률|추천|오를\s*(?:것|거)|내릴\s*(?:것|거)|상승할\s*(?:것|거)|하락할\s*(?:것|거)"
                              r"|target\s*price|probability|recommend|will\s+(?:rise|fall)|目標株価|確率|推奨|目标价|概率|推荐", re.I)
_HANGUL = re.compile(r"[가-힣]")
_FIRST_SENTENCE = re.compile(r"(?<=[.!?。！？])\s*")
_KANA = re.compile(r"[぀-ヿ]")
_HAN = re.compile(r"[一-鿿]")
LANGUAGE_NAME = {"ko-KR": "한국어", "en-US": "영어(English)", "ja-JP": "일본어(日本語)", "zh-CN": "중국어 간체(简体中文)"}


class TooManyVersions(Exception):
    pass


# ---------- 입력 ----------


def _series(db: Session, now: datetime) -> dict[str, list[tuple[date, float]]]:
    out: dict[str, list] = {}
    for key, code in (("kospi", "KS11"), ("kosdaq", "KQ11")):
        rows = db.execute(select(MarketIndex.price_date, MarketIndex.close_price).where(MarketIndex.index_code == code)
                          .order_by(MarketIndex.price_date.desc()).limit(SERIES_ROWS)).all()
        out[key] = drop_open_session([(r[0], float(r[1])) for r in rows if r[1] is not None], now)
    rows = db.execute(select(ExchangeRate.price_date, ExchangeRate.close_price).where(ExchangeRate.pair_code == PAIR_CODE)
                      .order_by(ExchangeRate.price_date.desc()).limit(SERIES_ROWS)).all()
    out["usd_krw"] = drop_open_session([(r[0], float(r[1])) for r in rows if r[1] is not None], now)
    return out


def _market_news(db: Session, now: datetime) -> list[dict]:
    rows = db.execute(select(News.title, News.source, News.url, News.published_at)
                      .where(News.is_relevant.isnot(False), News.published_at >= now - timedelta(days=MARKET_NEWS_DAYS),
                             News.published_at <= now, News.title.op("~*")(MARKET_NEWS_RE))
                      .order_by(News.published_at.desc()).limit(MARKET_NEWS_FETCH)).all()
    items = [{"title": r.title, "source": r.source, "url": r.url, "published_at": r.published_at.astimezone(KST).isoformat()} for r in rows]
    return sorted(items, key=lambda i: not MARKET_INDEX_RE.search(i["title"] or ""))  # 안정 정렬: 같은 묶음 안에서는 최신순


def _research(db: Session, user_id: int, companies: dict[str, Company]) -> dict[str, dict]:
    """종목별 이 사용자의 마지막 리서치 시각과 그 뒤 저장된(발행·접수 시각 기준) 뉴스·공시 수."""
    by_id = {c.id: t for t, c in companies.items()}
    if not by_id:
        return {}
    last = dict(db.execute(select(ResearchReport.company_id, func.max(ResearchReport.created_at))
                           .where(ResearchReport.user_id == user_id, ResearchReport.company_id.in_(list(by_id)))
                           .group_by(ResearchReport.company_id)).all())
    out = {}
    for cid, at in last.items():
        news = db.execute(select(func.count()).select_from(News).where(News.company_id == cid, News.published_at > at,
                                                                      News.is_relevant.isnot(False))).scalar()
        disc = db.execute(select(func.count()).select_from(Disclosure).where(Disclosure.company_id == cid,
                                                                            Disclosure.disclosed_at > at)).scalar()
        out[by_id[cid]] = {"at": at, "news": int(news or 0), "disclosures": int(disc or 0)}
    return out


def gather(db: Session, user_id: int, owner_key: str, now: datetime, tz_name: str) -> dbf.Facts:
    """읽기 전용(계좌를 만들지 않고 commit하지 않는다). 사용자 데이터는 owner_key·user_id로만 읽는다."""
    now = now.astimezone(KST)
    inputs = load_inputs(db, owner_key, NEWS_FETCH, now)
    brief = build_briefing(inputs, NEWS_FETCH, now)
    held = {h.ticker for h in inputs.holdings}
    tickers = [i.ticker for i in inputs.holdings] + [w.ticker for w in inputs.watchlist if w.ticker not in held]
    companies = {c.ticker: c for c in db.execute(select(Company).where(Company.ticker.in_(tickers))).scalars()} if tickers else {}
    base = brief.get("data_date")
    recent = db.execute(select(DataRefreshLog).where(DataRefreshLog.step.in_(REFRESH_STEPS))
                        .order_by(DataRefreshLog.id.desc()).limit(MAX_STEPS_PER_RUN)).scalars().all()
    indirect = {}
    since = now - timedelta(days=RECENT_DAYS)
    for scope in ("holdings", "watchlist"):
        for it in brief.get(scope) or []:
            c = companies.get(it["ticker"])
            if c is not None:
                indirect[it["ticker"]] = related_news.indirect_news(db, c, since, exclude_urls=[n.get("url") for n in it["news"]])
    return dbf.Facts(
        now=now, timezone=tz_name, brief=brief,
        bars={k: drop_open_session(v, now, lambda b: b.date) for k, v in inputs.bars.items()},
        index_series=_series(db, now),
        price_status=price_status(db, date.fromisoformat(base), now) if base else None,
        last_refresh=summarize_last_run(recent),
        news_collected_at=db.execute(select(func.max(News.created_at))).scalar(),
        disclosure_collected_at=db.execute(select(func.max(Disclosure.created_at))).scalar(),
        market_news=_market_news(db, now),
        indirect=indirect,
        research=_research(db, user_id, companies),
        markets={t: c.market for t, c in companies.items()},
        exposure=get_exposure(db, owner_key, 10, now) if inputs.holdings else None,
        watch_dedup=sum(1 for w in inputs.watchlist if w.ticker in held),
        intraday=session_is_open(now),
        known_names=list(db.execute(select(Company.name)).scalars()),
    )


# ---------- LLM 요약 ----------

SYSTEM_PROMPT = """너는 일일 투자 브리핑의 요약 한 단락을 쓰는 도우미다. 입력 JSON은 코드가 DB 값으로 계산한 브리핑 내용이다.
규칙:
1. 입력에 있는 숫자·날짜·종목명만 쓴다. 숫자는 입력 표기 그대로 옮기고 새로 계산하지 않는다.
2. 입력에 없는 뉴스·업종·사건·전망·확률·목표 주가를 만들지 않는다. 오르거나 내릴 것이라고 예측하지 않는다.
3. 매수·매도를 권하거나 지시하지 않는다.
4. freshness가 지연을 말하면 첫 문장에 그 기준일을 밝힌다. 오래된 데이터를 오늘 시장처럼 쓰지 않는다.
5. 2~4문장, {language}로만 쓴다. 종목명과 뉴스·공시 제목은 입력 그대로 쓰고 번역하지 않는다. 숫자를 나열하지 말고 꼭 필요한 것만 쓴다.
6. 출력은 JSON 하나만: {{"summary": "..."}}"""

_client: ollama.Client | None = None


def _chat(messages: list[dict]) -> str:
    """Ollama 호출(JSON 모드). 주소는 OLLAMA_HOST, 모델은 기존 설정(llm_settings.chat_model) 그대로. 테스트는 이 함수를 바꿔 끼운다."""
    global _client
    if _client is None:
        _client = ollama.Client(timeout=LLM_TIMEOUT_SECONDS)
    resp = _client.chat(model=chat_model(), messages=messages, format="json", options={"temperature": 0.2}, **call_options())
    return resp["message"]["content"]


def _other_companies(text: str, names: list[str], known: list[str]) -> list[str]:
    """브리핑 종목이 아닌 DB 종목명이 text에 있는지(2글자 이상 한글 이름, 5글자 이상 영문 이름만: 짧은 이름은 다른 낱말의 일부로 걸린다)."""
    rest = text
    for n in sorted(names, key=len, reverse=True):
        rest = rest.replace(n, " ")
    found = []
    for n in sorted(set(known) - set(names), key=len, reverse=True):
        if (len(n) >= 2 and _HANGUL.search(n) or len(n) >= 5) and n in rest:
            found.append(n)
            rest = rest.replace(n, " ")
    return sorted(found)


def validate_summary(content: str, payload: dict, locale: str, names: list[str], known: list[str] = (),
                     keep: list[str] = ()) -> tuple[str | None, str | None]:
    """(요약, None) 또는 (None, 이유). 숫자는 payload에 있는 값(표기 변형 포함)만, 금지 표현·브리핑에 없는 종목명·다른 언어 글자 없음.
    시세가 지연·오래됨이면 기준일(data_as_of)을 밝혀야 한다. keep(원문 그대로 두는 뉴스·공시 제목)과 종목명은 언어 글자 검사에서 뺀다."""
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return None, "JSON 아님"
    text = data.get("summary") if isinstance(data, dict) else None
    if not isinstance(text, str) or not text.strip():
        return None, "summary 없음"
    text = text.strip()
    if len(text) > SUMMARY_MAX_CHARS:
        return None, "너무 김"
    first = _FIRST_SENTENCE.split(text, maxsplit=1)[0]
    if payload.get("market_data_status") in (dbf.STATUS_DELAYED, dbf.STATUS_STALE) and payload.get("data_as_of") not in first:
        return None, f"첫 문장에 지연된 시세의 기준일({payload.get('data_as_of')})을 밝히지 않음"
    bad = find_disallowed_numbers(text, allowed_numbers(payload))
    if bad:
        return None, f"입력에 없는 숫자 {bad[:5]}"
    hits = [p for p in FORBIDDEN_PHRASES if p in text] + [m.group(0) for m in _EXTRA_FORBIDDEN.finditer(text)]
    if hits:
        return None, f"금지 표현 {hits[:3]}"
    unknown = sorted(set(find_unknown_companies(text, names)) | set(_other_companies(text, names, list(known))))
    if unknown:
        return None, f"입력에 없는 종목 {unknown[:3]}"
    if locale == "ko-KR":
        if not _HANGUL.search(text):
            return None, "한국어 아님"
    else:
        rest = text
        for n in sorted([*names, *keep], key=len, reverse=True):
            rest = rest.replace(n, "")
        if _HANGUL.search(rest):
            return None, "한글이 남음"
        if locale == "en-US" and (_KANA.search(rest) or _HAN.search(rest)):
            return None, "영어 아님"
        if locale == "ja-JP" and not _KANA.search(rest):
            return None, "일본어 아님"
        if locale == "zh-CN" and (_KANA.search(rest) or not _HAN.search(rest)):
            return None, "중국어 아님"
    return text, None


def llm_summary(f: dbf.Facts, locale: str) -> tuple[str | None, str | None]:
    """(검증 통과한 요약, None) 또는 (None, 실패 사유). 연결 실패·타임아웃은 재시도 없이 바로 포기(코드 요약으로)."""
    payload = dbf.llm_payload(f, locale)
    names = [t["name"] for t in dbf.tickers(f)]
    keep = [x["title"] for x in dbf._sources(f) if x.get("title")]
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(language=LANGUAGE_NAME[locale])},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    reason = None
    for attempt in range(LLM_MAX_ATTEMPTS):
        try:
            content = _chat(messages)
        except Exception as e:  # 연결 실패, 타임아웃, 모델 없음 등
            logger.warning("브리핑 요약 LLM 호출 실패(%s), 코드 요약으로 대체", type(e).__name__)
            return None, f"LLM 호출 실패({type(e).__name__})"
        text, reason = validate_summary(content, payload, locale, names, f.known_names, keep)
        if text is not None:
            return text, None
        logger.info("브리핑 요약 검증 실패(%d회차): %s", attempt + 1, reason)
        messages = messages[:2] + [{"role": "assistant", "content": content},
                                   {"role": "user", "content": f"규칙 위반: {reason}. 규칙을 지켜 JSON 하나만 다시 출력해."}]
    return None, f"출력 검증 실패({reason})"


# ---------- 저장·조회 ----------


def local_today(now: datetime, tz: ZoneInfo) -> date:
    return now.astimezone(tz).date()


def _latest(db: Session, user_id: int, day: date) -> DailyBriefing | None:
    return db.execute(select(DailyBriefing).where(DailyBriefing.user_id == user_id, DailyBriefing.local_date == day)
                      .order_by(DailyBriefing.version.desc()).limit(1)).scalar_one_or_none()


def _versions(db: Session, user_id: int, day: date) -> int:
    return int(db.execute(select(func.count()).select_from(DailyBriefing)
                          .where(DailyBriefing.user_id == user_id, DailyBriefing.local_date == day)).scalar() or 0)


def build(f: dbf.Facts, locale: str, use_llm: bool) -> dict:
    """저장할 값: content(locale)·original_content(ko-KR)·요약 출처·LLM 실패 사유(내부 기록용, 응답에 내보내지 않는다)."""
    text, reason = llm_summary(f, locale) if use_llm else (None, "use_llm=false")
    content = dbf.render(f, locale, text)
    original = content if locale == "ko-KR" else dbf.render(f, "ko-KR")
    return {"content": content, "original": original, "summary_source": "llm" if text else "code", "llm_failure": reason}


def generate(db: Session, user_id: int, owner_key: str, locale: str, tz: ZoneInfo, regenerate: bool = False,
             use_llm: bool = True, now: datetime | None = None) -> tuple[DailyBriefing, bool]:
    """(브리핑, 새로 만들었는지). 오늘 것이 있고 regenerate가 아니면 그대로 돌려준다(다시 계산하지 않는다)."""
    now = (now or datetime.now(timezone.utc)).astimezone(KST)
    day = local_today(now, tz)
    existing = _latest(db, user_id, day)
    if existing is not None and not regenerate:
        return existing, False
    if existing is not None and _versions(db, user_id, day) >= MAX_VERSIONS:
        raise TooManyVersions()
    f = gather(db, user_id, owner_key, now, tz.key)
    out = build(f, locale, use_llm)
    if out["llm_failure"]:
        logger.info("일일 브리핑 요약은 코드 요약(%s)", out["llm_failure"])
    c = out["content"]
    row = DailyBriefing(
        user_id=user_id, local_date=day, timezone=tz.key, locale=locale, version=(existing.version + 1) if existing else 1,
        generated_at=now, data_as_of=date.fromisoformat(c["data_as_of"]) if c["data_as_of"] else None,
        market_data_status=c["market_data_status"], summary_source=out["summary_source"],
        content=c, original_content=out["original"], tickers=dbf.tickers(f), sources=dbf._sources(f), section_status=c["section_status"],
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:  # 같은 사용자의 동시 생성: 먼저 저장된 것을 돌려준다
        db.rollback()
        latest = _latest(db, user_id, day)
        if latest is None:
            raise
        return latest, False
    db.refresh(row)
    return row, True


def current(db: Session, user_id: int, tz: ZoneInfo, now: datetime | None = None) -> DailyBriefing | None:
    return _latest(db, user_id, local_today(now or datetime.now(timezone.utc), tz))


def get(db: Session, user_id: int, briefing_id: int) -> DailyBriefing | None:
    return db.execute(select(DailyBriefing).where(DailyBriefing.id == briefing_id, DailyBriefing.user_id == user_id)).scalar_one_or_none()


def history(db: Session, user_id: int, date_from: date | None, date_to: date | None, page: int, size: int,
            latest_only: bool = True) -> tuple[list[DailyBriefing], int, dict]:
    """(행들, 전체 수, 날짜별 버전 수). 날짜(local_date) 내림차순, 같은 날이면 버전 내림차순. latest_only면 날짜마다 마지막 버전만."""
    where = [DailyBriefing.user_id == user_id]
    if date_from:
        where.append(DailyBriefing.local_date >= date_from)
    if date_to:
        where.append(DailyBriefing.local_date <= date_to)
    q = select(DailyBriefing).where(*where)
    if latest_only:
        newest = (select(DailyBriefing.local_date, func.max(DailyBriefing.version).label("v")).where(*where)
                  .group_by(DailyBriefing.local_date).subquery())
        q = q.join(newest, (newest.c.local_date == DailyBriefing.local_date) & (newest.c.v == DailyBriefing.version))
    total = db.execute(select(func.count()).select_from(q.subquery())).scalar() or 0
    rows = db.execute(q.order_by(DailyBriefing.local_date.desc(), DailyBriefing.version.desc())
                      .offset((page - 1) * size).limit(size)).scalars().all()
    days = list({r.local_date for r in rows})
    counts = dict(db.execute(select(DailyBriefing.local_date, func.count()).where(DailyBriefing.user_id == user_id,
                                                                                   DailyBriefing.local_date.in_(days))
                             .group_by(DailyBriefing.local_date)).all()) if days else {}
    return list(rows), int(total), counts


def serialize(row: DailyBriefing, tz: ZoneInfo, created: bool | None = None) -> dict:
    out = {
        "id": row.id, "version": row.version, "local_date": row.local_date.isoformat(), "timezone": row.timezone, "locale": row.locale,
        "generated_at": row.generated_at.astimezone(KST).isoformat(),
        "data_as_of": row.data_as_of.isoformat() if row.data_as_of else None,
        "market_data_status": row.market_data_status, "summary_source": row.summary_source,
        "section_status": row.section_status, "tickers": row.tickers, "sources": row.sources, "content": row.content,
        "original_content": row.original_content if row.locale != "ko-KR" else None,
        **{k: v for k, v in date_fields(row.generated_at, tz).items() if k == "date_group"},
    }
    if created is not None:
        out["created"] = created
    return out


def serialize_item(row: DailyBriefing, tz: ZoneInfo, versions: int) -> dict:
    c = row.content or {}
    return {
        "id": row.id, "version": row.version, "versions_on_date": versions, "local_date": row.local_date.isoformat(),
        "generated_at": row.generated_at.astimezone(KST).isoformat(), "locale": row.locale,
        "data_as_of": row.data_as_of.isoformat() if row.data_as_of else None, "market_data_status": row.market_data_status,
        "summary_source": row.summary_source, "title": c.get("title"), "headline": (c.get("freshness") or {}).get("headline"),
        "summary": (c.get("summary") or {}).get("text"), "section_status": row.section_status,
        "ticker_count": len(row.tickers or []), "detail_path": f"/api/briefings/daily/{row.id}",
        # local_date는 저장된 값(생성 당시 사용자 시간대의 날짜). 표시용 시각·날짜 그룹만 조회 시간대로 붙인다
        **{k: v for k, v in date_fields(row.generated_at, tz).items() if k != "local_date"},
    }
