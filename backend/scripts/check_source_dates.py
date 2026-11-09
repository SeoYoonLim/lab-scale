"""뉴스·공시 날짜와 출처 정합성 점검(2026-11-04, 26차). DB 읽기만, 외부 요청 없음.

    python -m scripts.check_source_dates

- 공시: 저장 접수 시각(disclosed_at)의 UTC 날짜와 한국 시간 날짜가 다른 건수(UTC 문자열을 잘라 쓰면 하루 이른 날짜가 나오는 건수),
  DART 접수번호(앞 8자리) 날짜와 한국 시간 접수일이 다른 건, 접수번호가 없는 건. 답변은 접수번호 날짜를 쓴다(app.outlook.disclosure_date).
- 정정공시: 제목이 [기재정정]·[첨부정정] 등으로 시작하는 건수(원공시와 구분해 표시된다).
- 뉴스: UTC 날짜와 한국 시간 날짜가 다른 건수, 발행일이 수집일보다 늦은 건, 같은 URL이 여러 종목에 저장된 건(직접·간접 섹션 중복의 원인 후보).
- 잠정실적 공시: 접수번호 날짜와 한국 시간 접수일이 같은지(실적 줄의 "접수·발표일").
외부 원본(DART 원문·언론사 페이지)과의 대조는 하지 않는다(확인 불가로 보고).
"""

import sys

from dotenv import load_dotenv
from sqlalchemy import text

QUERIES = (
    ("공시 전체", "select count(*) from disclosure"),
    ("공시 UTC 날짜 != 한국 시간 날짜", "select count(*) from disclosure where (disclosed_at at time zone 'UTC')::date "
                                   "<> (disclosed_at at time zone 'Asia/Seoul')::date"),
    ("공시 접수번호 없음", "select count(*) from disclosure where source_url is null or source_url !~ 'rcptNo=\\d{14}'"),
    ("공시 접수번호 날짜 != 한국 시간 접수일", "select count(*) from disclosure where source_url ~ 'rcptNo=\\d{14}' and "
                                       "substring(source_url from 'rcptNo=(\\d{8})') <> to_char(disclosed_at at time zone 'Asia/Seoul', 'YYYYMMDD')"),
    ("정정공시", "select count(*) from disclosure where title ~ '^\\[[^]]*(정정|추가)'"),
    ("잠정실적 공시", "select count(*) from disclosure where title like '%(잠정)실적%'"),
    ("잠정실적 공시 접수번호 날짜 != 한국 시간 접수일", "select count(*) from disclosure where title like '%(잠정)실적%' and "
                                              "substring(source_url from 'rcptNo=(\\d{8})') <> to_char(disclosed_at at time zone 'Asia/Seoul', 'YYYYMMDD')"),
    ("뉴스 전체", "select count(*) from news"),
    ("뉴스 UTC 날짜 != 한국 시간 날짜", "select count(*) from news where (published_at at time zone 'UTC')::date "
                                   "<> (published_at at time zone 'Asia/Seoul')::date"),
    ("뉴스 발행일 > 수집일 + 1시간", "select count(*) from news where published_at > created_at + interval '1 hour'"),
    ("뉴스 같은 URL이 여러 종목", "select count(*) from (select url from news group by url having count(distinct company_id) > 1) x"),
)
DETAIL = ("select c.name, d.title, to_char(d.disclosed_at at time zone 'Asia/Seoul', 'YYYY-MM-DD'), substring(d.source_url from 'rcptNo=(\\d{14})') "
          "from disclosure d join company c on c.id = d.company_id where d.source_url ~ 'rcptNo=\\d{14}' and "
          "substring(d.source_url from 'rcptNo=(\\d{8})') <> to_char(d.disclosed_at at time zone 'Asia/Seoul', 'YYYYMMDD') order by 1")


def main() -> int:
    load_dotenv()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        for name, q in QUERIES:
            print(f"{name}: {db.execute(text(q)).scalar():,}")
        print("접수번호 날짜와 다른 공시:")
        for name, title, kst, rcept in db.execute(text(DETAIL)):
            print(f"  {name} | {title} | 한국 시간 접수일 {kst} | 접수번호 {rcept}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
