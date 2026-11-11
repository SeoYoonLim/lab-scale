"""(2026-11-03) 간접 관련 뉴스: 업종·산업 뉴스(주식)와 기초지수 관련 뉴스(지수형 ETF).

직접 관련 뉴스(제목에 그 종목 이름, app.news_relevance.evidence_news)와 섞지 않는다. 신호 판정에도 쓰지 않는다. 직접 관련 뉴스가
0건이어도 간접 뉴스로 채우지 않고 별도 섹션·필드로만 보인다(전망 답변의 "업종·산업 뉴스(간접)"/"기초지수 관련 뉴스(간접)",
news_tool의 indirect_news).

업종 매핑: company.sector가 비어 있어(350종목 모두 NULL) DB에 이미 있는 업종 ETF 구성종목(etf_holding, 운용사 공시)으로 만든다.
SECTOR_SOURCES의 ETF 최신 구성종목에 종목코드(또는 이름)가 있으면 그 업종이다. 여러 업종에 걸리면 SECTOR_SOURCES 순서가 앞인 것.
그 밖의 종목은 업종 미분류라 업종 뉴스를 보이지 않는다(외부 업종 분류를 새로 수집하지 않았다).
"""

import re
from datetime import datetime

from app.display_dates import kst_iso
from app.news_relevance import is_duplicate_title, title_names_subject

# 업종 -> 구성종목을 업종 근거로 쓰는 업종 ETF(우선순위 순)
SECTOR_SOURCES: dict[str, tuple[str, ...]] = {
    "반도체": ("KODEX 반도체", "TIGER 반도체TOP10", "KODEX AI반도체TOP2플러스"),
    "2차전지": ("KODEX 2차전지산업",),
    "전력설비": ("KODEX AI전력핵심설비",),
}
SECTOR_KEYWORDS: dict[str, str] = {
    "반도체": r"반도체|HBM|메모리|D램|디램|DRAM|낸드|NAND|파운드리",
    "2차전지": r"2차\s*전지|이차\s*전지|배터리|양극재|음극재|전해질|리튬",
    "전력설비": r"전력\s*기기|변압기|전력망|송배전|전선|전력\s*설비",
}
# 지수형 ETF의 기초지수: 이름 패턴 -> (기초지수 이름, 제목 정규식). 위에서부터 첫 일치. 레버리지·인버스는 이름에 지수가 없으면 코스피200.
INDEX_RULES: tuple[tuple[str, str, str], ...] = (
    (r"나스닥\s*100", "나스닥100", r"나스닥\s*100|NASDAQ\s*100"),
    (r"S&P\s*500", "S&P500", r"S&P\s*500|에스앤피\s*500"),
    (r"필라델피아\s*반도체", "필라델피아반도체지수", r"필라델피아\s*반도체|SOX"),
    (r"다우존스", "다우존스", r"다우존스|다우\s*지수"),
    (r"코스닥\s*150", "코스닥150", r"코스닥\s*150|KOSDAQ\s*150"),
    (r"(?:KODEX|TIGER|RISE|ACE|PLUS|SOL|HANARO)\s*200(?!\s*(?:TR|IT|타겟|미국))|^(?:KODEX|TIGER)\s*(?:레버리지|인버스)$",
     "코스피200", r"코스피\s*200|KOSPI\s*200"),
)
INDIRECT_LIMIT = 3
INDIRECT_FETCH = 60
SECTOR_LABEL = "업종·산업 뉴스(간접)"
INDEX_LABEL = "기초지수 관련 뉴스(간접)"

_SECTOR_CACHE: dict[str, dict[str, str]] = {}


def index_rule(company) -> tuple[str, str] | None:
    """지수형 ETF면 (기초지수 이름, 제목 정규식), 아니면 None."""
    if getattr(company, "asset_type", None) != "etf":
        return None
    for name_rx, label, title_rx in INDEX_RULES:
        if re.search(name_rx, company.name, re.IGNORECASE):
            return label, title_rx
    return None


def sector_map(db) -> dict[str, str]:
    """종목코드 -> 업종(SECTOR_SOURCES의 ETF 최신 구성종목). 프로세스 안에서 한 번만 읽는다."""
    if "map" in _SECTOR_CACHE:
        return _SECTOR_CACHE["map"]
    from sqlalchemy import func

    from app.models import Company, EtfHolding

    out: dict[str, str] = {}
    names = {n: t for n, t in db.query(Company.name, Company.ticker).filter(Company.asset_type == "stock")}
    for sector, etfs in SECTOR_SOURCES.items():
        for etf in etfs:
            cid = db.query(Company.id).filter(Company.name == etf).scalar()
            if cid is None:
                continue
            latest = db.query(func.max(EtfHolding.as_of_date)).filter(EtfHolding.company_id == cid).scalar()
            for code, name in db.query(EtfHolding.holding_code, EtfHolding.holding_name).filter(
                    EtfHolding.company_id == cid, EtfHolding.as_of_date == latest):
                ticker = code if code in names.values() else names.get(name)
                if ticker:
                    out.setdefault(ticker, sector)
    _SECTOR_CACHE["map"] = out
    return out


def sector_of(db, company) -> str | None:
    if getattr(company, "asset_type", None) != "stock":
        return None
    return sector_map(db).get(company.ticker)


def indirect_news(db, company, since: datetime | None, exclude_urls=(), limit: int = INDIRECT_LIMIT) -> tuple[str | None, str | None, list[dict]]:
    """(섹션 이름, 업종/기초지수 이름, 기사들). 해당 없으면 (None, None, []).

    업종: 업종 키워드가 제목에 있는 최근 기사(모든 종목의 저장 뉴스, is_relevant가 False가 아닌 것). 기초지수: 기초지수 이름이 제목에
    있는 기사. 둘 다 제목에 요청 종목 이름이 있는 기사(직접 관련)와 exclude_urls는 빼고, 비슷한 제목은 하나만 남긴다."""
    from app.models import News

    rule = index_rule(company)
    if rule is not None:
        section, topic, title_rx = INDEX_LABEL, rule[0], rule[1]
    else:
        topic = sector_of(db, company)
        if topic is None:
            return None, None, []
        section, title_rx = SECTOR_LABEL, SECTOR_KEYWORDS[topic]
    q = db.query(News.title, News.url, News.source, News.published_at).filter(
        News.is_relevant.isnot(False), News.title.op("~*")(title_rx))
    if since is not None:
        q = q.filter(News.published_at >= since)
    kept: list[dict] = []
    seen_urls = set(exclude_urls)
    for title, url, source, published in q.order_by(News.published_at.desc()).limit(INDIRECT_FETCH):
        if url in seen_urls or title_names_subject(company.name, company.asset_type, title):
            continue
        if any(is_duplicate_title(title, k["title"]) for k in kept):
            continue
        seen_urls.add(url)
        # 42차(2026-11-11): KST 문자열로(app.display_dates.kst_iso, 39차 직접 뉴스와 같은 규칙). 전에는 DB timestamptz를 그대로
        # isoformat해 UTC(+00:00)로 나가, 일일 브리핑 응답·출처의 간접 뉴스 시각이 KST 계약과 달랐고 news_tool이 LLM에 넘기는
        # 간접 뉴스도 앞 10자가 UTC 날짜였다(KST 00~09시 기사는 하루 전 날짜).
        kept.append({"title": title, "url": url, "source": source, "published_at": kst_iso(published)})
        if len(kept) >= limit:
            break
    return section, topic, kept
