"""클라우드(GitHub Actions) 시장 데이터 수집 -> 암호화 패키지(2026-11-23). DB 없이 돈다(집 PC가 꺼져 있어도 된다).

기존 수집기의 원천 조회·확정 규칙을 그대로 쓰고, DB 저장 대신 패키지(app/market_package)로 내보낸다.
- 주식: app.official_close.official_records(공식 종가 = 다음 거래일 기준가로 복원, 최신 거래일은 확정 전이라 없음) + FDR 원천 행
- ETF: 같은 함수(asset_type=etf, 마감 뒤에만 최신일)
- 지수: collectors.fetch_index_frame(FDR 폴백 체인) + confirm_index_records(네이버 지수 일별 대조, 장중·불일치 보류)
- 환율: FDR(Yahoo KRW=X) + collectors.fx_records(끝나지 않은 런던 일봉 제외, 날짜 정규화는 기존과 같음)
- 뉴스: collectors.fetch_naver_news + news_recovery.paginate(기간 안 기사만, 원문 API 응답 항목: 제목·링크·요약·게시 시각)
- 공시: collectors.get_dart_corp_code + fetch_dart_disclosures(목록 항목만. 본문은 받지 않는다)
걸러내기·중복 제거·저장 전 검증은 집 PC 동기화(app/market_data_sync)가 기존 저장 함수로 한다.

사용 예:
  python -m scripts.cloud_market_data --out out/            # 수집 -> out/packages/<날짜>/<실행>/ (키 필요)
  python -m scripts.cloud_market_data --out out/ --sections stocks,etfs --limit 5
  python -m scripts.cloud_market_data --publish-dir data-branch/ --out out/   # 검증 뒤 market-data 작업 폴더에 복사·latest.json 갱신
종료 코드: 0 성공(일부 구역 실패 포함, manifest에 기록) / 1 시세 구역 전부 실패 / 2 키 없음 / 3 오늘 수집할 것 없음(휴장일 등, --force로 무시)
비밀값(API 키·MARKET_DATA_KEY)은 출력하지 않는다. 오류 문장도 collectors._safe_error로 키를 가린다.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from app import krx_calendar
from app import market_package as P

UNIVERSE_FILE = Path(__file__).resolve().parent / "cloud_universe.json"
PRICE_DAYS = 10  # 달력 일. 놓친 날(예약 실행이 밀린 날)을 다음 실행이 채운다. 집 동기화는 없는 행만 넣는다
INDEX_DAYS = 14
FX_DAYS = 10
NEWS_DAYS = 2  # 뉴스·공시 조회 기간(달력 일, 오늘 포함). 실행이 하루에 여러 번이라 겹쳐도 동기화가 중복을 건너뛴다
NEWS_MAX_PAGES = 1
NEWS_PER_COMPANY = 40  # 실행마다 종목당 최신 40건(데이터 브랜치 크기 제한. 예전 정기 수집은 종목당 10건)
DELAY = 0.1


def log(msg: str) -> None:
    print(msg, flush=True)


def kst_now() -> datetime:
    return datetime.now(krx_calendar.KST)


def load_universe(path: Path = UNIVERSE_FILE) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))["companies"]
    return [r for r in rows if r.get("ticker") and r.get("name") and r.get("asset_type") in ("stock", "etf")]


def plan(now: datetime) -> dict:
    """오늘 실행이 할 일. data_date = 종가가 있어야 할 가장 최근 거래일(krx_calendar.expected_trading_day)."""
    today = now.date()
    data_date = krx_calendar.expected_trading_day(now)
    trading_today = krx_calendar.day_status(today) == krx_calendar.TRADING
    # 휴장일·주말에는 새로 생길 데이터가 없다. 다만 휴장일 다음 거래일 아침 실행은 직전 거래일 주식 종가를 확정한다(다음 거래일이 열려야 확정).
    useful = trading_today or (today - data_date).days <= 1
    return {"today": today, "data_date": data_date, "is_trading_day": trading_today, "useful": useful}


def _safe(e: Exception) -> str:
    from app import collectors as C

    return C._safe_error(e)


# ---------- 구역별 수집 ----------


def collect_prices(universe: list[dict], asset_type: str, now: datetime, days: int = PRICE_DAYS, limit: int | None = None,
                   sleep=time.sleep) -> dict:
    import FinanceDataReader as fdr

    from app import collectors as C, official_close

    targets = [u for u in universe if u["asset_type"] == asset_type][:limit]
    start = (now - timedelta(days=days)).date()
    rows, failures, ok = [], [], 0
    pending = 0
    for u in targets:
        t = u["ticker"]
        try:
            df = C._read_fdr(fdr.DataReader, t, (start - timedelta(days=14)).isoformat(), now.date().isoformat())
            source = {i.date(): C._price_row(i, r) for i, r in df.dropna(subset=["Close"]).iterrows()} if df is not None and not df.empty else {}
            recs, stats = official_close.official_records(t, asset_type, start, now=now.replace(tzinfo=None), source=source)
            pending += stats.get("skipped_provisional", 0)
            for r in recs:
                if r["price_date"] > now.date() or r["close_price"] is None or r["close_price"] <= 0:
                    continue
                rows.append({"date": r["price_date"].isoformat(), "ticker": t, "name": u["name"], "asset_type": asset_type,
                             "open": r.get("open_price"), "high": r.get("high_price"), "low": r.get("low_price"),
                             "close": r["close_price"], "volume": r.get("volume"), "change_pct": r.get("change_pct")})
            ok += 1
        except Exception as e:  # noqa: BLE001 - 종목 단위로 실패를 기록하고 계속한다
            failures.append({"ticker": t, "error": _safe(e)[:200]})
        sleep(DELAY)
    status = P.SECTION_OK if not failures else P.SECTION_PARTIAL if ok else P.SECTION_FAILED
    note = (f"최신 거래일 주식 종가는 다음 거래일 기준가로 확정한다(보류 {pending}종목·일)" if asset_type == "stock"
            else f"장 마감 전 최신일은 저장하지 않는다(보류 {pending}건)")
    return {"status": status, "rows": rows, "targets": len(targets), "succeeded": ok, "failed": len(failures), "failures": failures,
            "source": "네이버 일별 시세(m.stock.naver.com) 공식 종가 복원 + FinanceDataReader 원천 행" if asset_type == "stock"
            else "네이버 일별 시세 + FinanceDataReader", "note": note}


def collect_indices(now: datetime, days: int = INDEX_DAYS) -> dict:
    from app import collectors as C

    rows, failures, held = [], [], []
    start = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    for code in ("KS11", "KQ11"):
        try:
            df, source, errors = C.fetch_index_frame(code, start, now.strftime("%Y-%m-%d"), today=now.date())
            if df is None:
                failures.append({"code": code, "error": " | ".join(errors)[:300]})
                continue
            recs = [C._index_row(i, r) for i, r in df.sort_index().iterrows()]
            keep, h = C.confirm_index_records(code, recs, now)
            held += [{"code": code, **x} for x in h]
            rows += [{"date": r["price_date"].isoformat(), "code": code, "close": r["close_price"], "change_pct": r["change_pct"],
                      "source": source} for r in keep]
        except Exception as e:  # noqa: BLE001
            failures.append({"code": code, "error": _safe(e)[:200]})
    ok = 2 - len(failures)
    status = P.SECTION_OK if not failures else P.SECTION_PARTIAL if ok else P.SECTION_FAILED
    note = "확정 전(장중·네이버 대조 불일치·대조 불가) 보류: " + ", ".join(f"{x['code']} {x['date']}({x['status']})" for x in held) if held else None
    return {"status": status, "rows": rows, "targets": 2, "succeeded": ok, "failed": len(failures), "failures": failures,
            "source": "FinanceDataReader KRX 지수 캐시(폴백 FDR 별칭·Yahoo) + 네이버 지수 일별 대조", "note": note,
            "held": [{k: (v.isoformat() if isinstance(v, date) else v) for k, v in x.items()} for x in held]}


def collect_fx(now: datetime, days: int = FX_DAYS) -> dict:
    import FinanceDataReader as fdr

    from app import collectors as C

    try:
        df = C._read_fdr(fdr.DataReader, "USD/KRW", (now - timedelta(days=days)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d"))
        if df is None or df.empty:
            raise RuntimeError("USD/KRW 데이터를 받지 못했다")
        recs, incomplete = C.fx_records(df, None, now)
    except Exception as e:  # noqa: BLE001
        return {"status": P.SECTION_FAILED, "rows": [], "targets": 1, "succeeded": 0, "failed": 1,
                "failures": [{"pair": "USD/KRW", "error": _safe(e)[:200]}], "source": "Yahoo Finance KRW=X(FinanceDataReader)"}
    rows = [{"date": r["price_date"].isoformat(), "pair": "USD/KRW", "close": r["close_price"], "change_pct": r["change_pct"]}
            for r in recs]
    note = ("일봉 기준 시각: 런던(Europe/London) 하루. 끝나지 않은 일봉 제외: " + ", ".join(d.isoformat() for d in incomplete)
            if incomplete else "일봉 기준 시각: 런던(Europe/London) 하루")
    return {"status": P.SECTION_OK, "rows": rows, "targets": 1, "succeeded": 1, "failed": 0, "failures": [],
            "source": "Yahoo Finance KRW=X(FinanceDataReader)", "note": note}


NEWS_FIELDS = ("title", "originallink", "link", "description", "pubDate")


def collect_news(universe: list[dict], now: datetime, days: int = NEWS_DAYS, max_pages: int = NEWS_MAX_PAGES,
                 limit: int | None = None, fetch=None, sleep=time.sleep) -> dict:
    from app import collectors as C, news_recovery as R

    if fetch is None and not (os.environ.get("NAVER_CLIENT_ID") and os.environ.get("NAVER_CLIENT_SECRET")):
        return {"status": P.SECTION_SKIPPED, "rows": [], "note": "NAVER_CLIENT_ID/SECRET 없음(저장소 Secrets 설정 필요)"}
    if fetch is None:
        def fetch(query, display=10, sort="date", start=1):  # 한 쪽만, 종목당 NEWS_PER_COMPANY건
            return C.fetch_naver_news(query, display=min(display, NEWS_PER_COMPANY), sort=sort, start=start)
    since = datetime.combine(now.date() - timedelta(days=days - 1), datetime.min.time(), tzinfo=krx_calendar.KST)
    rows, failures, ok = [], [], 0
    for u in universe[:limit]:
        company = SimpleNamespace(name=u["name"], ticker=u["ticker"], asset_type=u["asset_type"])
        scan = R.paginate(fetch, R.news_query(company), since, now + timedelta(minutes=1), max_pages, delay=DELAY, sleep=sleep)
        if scan.error:
            failures.append({"ticker": u["ticker"], "error": C._API_KEY_RE.sub("***", scan.error)[:200]})
            if any(code in scan.error for code in ("401", "403", "429")):
                break  # 인증·호출 제한: 나머지 종목도 같다
            continue
        ok += 1
        for it in scan.items:
            rows.append({"date": (R.item_time(it) or now).astimezone(krx_calendar.KST).date().isoformat(), "ticker": u["ticker"],
                         **{k: it.get(k) for k in NEWS_FIELDS}})
        sleep(DELAY)
    targets = len(universe[:limit])
    status = P.SECTION_OK if ok == targets else P.SECTION_PARTIAL if ok else P.SECTION_FAILED
    return {"status": status, "rows": rows, "targets": targets, "succeeded": ok, "failed": targets - ok, "failures": failures,
            "source": "네이버 뉴스 검색 API(제목·링크·요약·게시 시각만, 기사 본문 없음)",
            "note": f"기간 {since.date().isoformat()}~{now.date().isoformat()} KST, 종목당 최신 최대 {NEWS_PER_COMPANY}건"}


DISCLOSURE_FIELDS = ("rcept_no", "rcept_dt", "corp_name", "flr_nm", "report_nm", "stock_code", "corp_code")


def collect_disclosures(universe: list[dict], days: int = NEWS_DAYS + 1, limit: int | None = None, sleep=time.sleep) -> dict:
    from app import collectors as C

    if not os.environ.get("DART_API_KEY"):
        return {"status": P.SECTION_SKIPPED, "rows": [], "note": "DART_API_KEY 없음(저장소 Secrets 설정 필요)"}
    stocks = [u for u in universe if u["asset_type"] == "stock"][:limit]
    rows, failures, ok = [], [], 0
    for u in stocks:
        try:
            corp = C.get_dart_corp_code(u["ticker"])
            if corp is None:
                raise RuntimeError("DART corp_code 없음")
            for it in C.fetch_dart_disclosures(corp, days=days):
                rd = it.get("rcept_dt") or ""
                rows.append({"date": f"{rd[:4]}-{rd[4:6]}-{rd[6:8]}" if len(rd) == 8 else None, "ticker": u["ticker"],
                             **{k: it.get(k) for k in DISCLOSURE_FIELDS}})
            ok += 1
        except Exception as e:  # noqa: BLE001
            msg = _safe(e)
            failures.append({"ticker": u["ticker"], "error": msg[:200]})
            if "[020]" in msg or any(f"[{c}]" in msg for c in ("010", "011", "012", "901")):
                break  # 요청 제한·키 오류
        sleep(DELAY)
    status = P.SECTION_OK if ok == len(stocks) else P.SECTION_PARTIAL if ok else P.SECTION_FAILED
    return {"status": status, "rows": rows, "targets": len(stocks), "succeeded": ok, "failed": len(stocks) - ok, "failures": failures,
            "source": "DART OpenAPI 공시 목록(list.json)", "note": f"최근 {days}일 접수분"}


# ---------- 패키지 쓰기·게시 ----------


def write_package(out: Path, manifest: dict, files: dict[str, bytes], run_tag: str) -> Path:
    d = out / P.manifest_dir(date.fromisoformat(manifest["data_date"]), run_tag)
    d.mkdir(parents=True, exist_ok=True)
    for name, blob in files.items():
        (d / name).write_bytes(blob)
    (d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


def verify_dir(d: Path, key: bytes) -> dict[str, int]:
    """쓴 패키지를 다시 읽어 검증한다(게시 전). 틀리면 PackageError."""
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    counts = {}
    for name, entry in manifest["sections"].items():
        if entry.get("file"):
            counts[name] = len(P.read_section(manifest, name, (d / entry["file"]).read_bytes(), key))
    return counts


def publish(pkg_dir: Path, out: Path, publish_dir: Path, manifest: dict) -> str:
    """검증한 패키지를 market-data 작업 폴더에 복사하고 latest.json·index.json을 갱신한다(커밋·push는 workflow가 한다)."""
    rel = pkg_dir.relative_to(out).as_posix()
    dest = publish_dir / rel
    if dest.exists():
        raise P.PackageError(f"이미 있는 패키지 경로: {rel}")
    shutil.copytree(pkg_dir, dest)
    summary = {"manifest": f"{rel}/manifest.json", "data_date": manifest["data_date"], "generated_at_kst": manifest["generated_at_kst"],
               "complete": manifest["complete"], "schema_version": manifest["schema_version"],
               "sections": {k: {"status": v["status"], "rows": v["rows"], "date_range": v.get("date_range")} for k, v in manifest["sections"].items()}}
    idx_path = publish_dir / "index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8")) if idx_path.exists() else {"schema_version": P.SCHEMA_VERSION, "packages": []}
    idx["packages"].append(summary)
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
    (publish_dir / "latest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    readme = publish_dir / "README.md"
    if not readme.exists():
        readme.write_text("# market-data\n\n클라우드 수집 시장 데이터 패키지(암호화). 앱 코드·.env 없음. 형식: backend/app/market_package.py, "
                          "만드는 곳: .github/workflows/market-data.yml, 읽는 곳: backend/app/market_data_sync.py\n", encoding="utf-8")
    return rel


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="market_data_out", help="패키지를 쓸 폴더")
    ap.add_argument("--publish-dir", default=None, help="market-data 브랜치 작업 폴더(주면 검증 뒤 복사·latest.json 갱신)")
    ap.add_argument("--sections", default=",".join(P.SECTIONS), help="쉼표 구분 구역")
    ap.add_argument("--limit", type=int, default=None, help="구역당 종목 수 제한(시험용)")
    ap.add_argument("--run-tag", default=None, help="패키지 폴더 이름(기본: KST 시각 + GitHub 실행 번호)")
    ap.add_argument("--force", action="store_true", help="휴장일에도 수집")
    ap.add_argument("--summary-file", default=None, help="결과 요약 JSON(비밀값 없음)")
    ap.add_argument("--collect-only", action="store_true", help="수집·건수 확인만(패키지·키 불필요). 작업 브랜치 검증용")
    args = ap.parse_args(argv)

    key = P.load_key()
    if key is None and not args.collect_only:
        log("MARKET_DATA_KEY가 없다(저장소 Secrets 또는 키 파일). 패키지를 암호화할 수 없어 멈춘다.")
        return 2
    now = kst_now()
    p = plan(now)
    log(f"KST {now:%Y-%m-%d %H:%M} · 오늘 거래일={p['is_trading_day']} · 데이터 기준일(종가가 있어야 할 최근 거래일)={p['data_date']}")
    if not p["useful"] and not args.force:
        log("오늘은 새로 생길 시장 데이터가 없다(휴장일·주말). 수집하지 않는다.")
        return 3
    wanted = [s.strip() for s in args.sections.split(",") if s.strip()]
    universe = load_universe()
    sections: dict[str, dict] = {}
    t0 = time.time()
    for name in wanted:
        log(f"[{name}] 수집 시작")
        if name == "stocks":
            sections[name] = collect_prices(universe, "stock", now, limit=args.limit)
        elif name == "etfs":
            sections[name] = collect_prices(universe, "etf", now, limit=args.limit)
        elif name == "indices":
            sections[name] = collect_indices(now)
        elif name == "fx":
            sections[name] = collect_fx(now)
        elif name == "news":
            sections[name] = collect_news(universe, now, limit=args.limit)
        elif name == "disclosures":
            sections[name] = collect_disclosures(universe, limit=args.limit)
        s = sections[name]
        log(f"[{name}] {s['status']} · 행 {len(s.get('rows') or [])} · 성공 {s.get('succeeded', '-')}/{s.get('targets', '-')}"
            + (f" · 실패 {s.get('failed')}" if s.get("failed") else "") + (f" · {s['note']}" if s.get("note") else ""))
        for f in (s.get("failures") or [])[:5]:
            log(f"    실패: {f}")
    run = {k: os.environ.get(v) for k, v in (("id", "GITHUB_RUN_ID"), ("attempt", "GITHUB_RUN_ATTEMPT"), ("event", "GITHUB_EVENT_NAME"),
                                               ("sha", "GITHUB_SHA"), ("repository", "GITHUB_REPOSITORY")) if os.environ.get(v)}
    run["runner"] = "github-actions" if os.environ.get("GITHUB_ACTIONS") else "local"
    run["elapsed_sec"] = round(time.time() - t0, 1)
    if args.collect_only:
        summary = {"collect_only": True, "data_date": p["data_date"].isoformat(), "run": run,
                   "sections": {k: {x: v.get(x) for x in ("status", "succeeded", "failed", "targets", "note")} | {
                       "rows": len(v.get("rows") or []),
                       "date_range": [min(r["date"] for r in v["rows"]), max(r["date"] for r in v["rows"])] if v.get("rows") else None,
                       "sample": (v.get("rows") or [{}])[-1] if k in ("indices", "fx") else None} for k, v in sections.items()}}
        if args.summary_file:
            Path(args.summary_file).write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        log(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
        price = [sections[n]["status"] for n in ("stocks", "etfs", "indices", "fx") if n in sections]
        return 1 if price and all(x == P.SECTION_FAILED for x in price) else 0
    manifest, files = P.build(p["data_date"], sections, key, generated_at=now, is_trading_day=p["is_trading_day"], run=run)
    tag = args.run_tag or f"{now:%Y%m%dT%H%M%S}" + (f"-r{run['id']}-{run.get('attempt', '1')}" if run.get("id") else "-local")
    out = Path(args.out)
    pkg = write_package(out, manifest, files, tag)
    counts = verify_dir(pkg, key)
    log(f"패키지 검증 통과: {pkg.as_posix()} {counts}")
    summary = {"package": pkg.relative_to(out).as_posix(), "data_date": manifest["data_date"], "complete": manifest["complete"],
               "sections": {k: {x: v.get(x) for x in ("status", "rows", "date_range", "succeeded", "failed", "targets", "note")}
                            for k, v in manifest["sections"].items()}}
    price_sections = [manifest["sections"][n]["status"] for n in ("stocks", "etfs", "indices", "fx") if n in wanted]
    price_failed = bool(price_sections) and all(s == P.SECTION_FAILED for s in price_sections)
    if args.publish_dir:
        if price_failed or not any(v["rows"] for v in manifest["sections"].values()):
            log("시세 구역이 모두 실패했거나 모든 구역이 비었다. 게시하지 않는다(정상 데이터로 공개하지 않음).")
            price_failed = True
        else:
            summary["published"] = publish(pkg, out, Path(args.publish_dir), manifest)
            log(f"게시 준비: {summary['published']}")
    if args.summary_file:
        Path(args.summary_file).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    log(json.dumps(summary, ensure_ascii=False, indent=1))
    return 1 if price_failed else 0


if __name__ == "__main__":
    sys.exit(main())
