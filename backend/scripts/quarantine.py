"""저장 전 검증에서 격리된 수집 데이터 관리 CLI(2026-11-08, 36차). 일반 사용자 API는 없다(관리자 역할 체계가 없다).

    python -m scripts.quarantine summary                        # 소스별·오류 코드별 미해결 격리 건수
    python -m scripts.quarantine list [--source stock_price] [--code close_nonpositive] [--status open] [--warnings] [--limit 50]
    python -m scripts.quarantine show ID                        # 상세(사유·payload·발생 횟수)
    python -m scripts.quarantine revalidate ID [ID ...] | --all-open [--source ...]   # 지금 규칙·DB로 다시 판정
    python -m scripts.quarantine reprocess ID [--apply]          # revalidated만 업무 테이블에 넣음(같은 키 데이터가 있으면 넣지 않음)
    python -m scripts.quarantine hold ID [--note "..."]          # 보류
    python -m scripts.quarantine discard ID [--note "..."]       # 버림(업무 테이블에 넣지 않음)
    python -m scripts.quarantine prune [--keep-days 180] [--include-open] [--dry-run]

reprocess는 --apply 없이 실행하면 무엇을 할지만 출력한다. 기존 정상 데이터는 어떤 명령도 덮어쓰지 않는다.
종료 코드: 0 성공, 1 대상 없음·처리 실패, 2 인자 오류.
"""

from __future__ import annotations

import argparse
import json
import sys


def _row(item) -> str:
    return (f"{item.id:>6} {item.source:<13} {item.validation_status:<11} {item.resolution_status:<13} {item.error_code:<26} "
            f"x{item.occurrences:<3} {item.last_seen_at:%Y-%m-%d %H:%M} {item.source_key[:60]}")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m scripts.quarantine", description="저장 전 검증 격리 데이터 관리")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("summary", help="소스별·오류 코드별 미해결 격리 건수")
    ls = sub.add_parser("list", help="격리 목록(최근 순)")
    ls.add_argument("--source")
    ls.add_argument("--code")
    ls.add_argument("--status", help="open | revalidated | reprocessed | kept_existing | on_hold | discarded")
    ls.add_argument("--warnings", action="store_true", help="격리 대신 경고(저장된 레코드)를 본다")
    ls.add_argument("--limit", type=int, default=50)
    sh = sub.add_parser("show", help="상세")
    sh.add_argument("id", type=int)
    rv = sub.add_parser("revalidate", help="다시 판정")
    rv.add_argument("ids", type=int, nargs="*")
    rv.add_argument("--all-open", action="store_true")
    rv.add_argument("--source")
    rp = sub.add_parser("reprocess", help="재검증 통과분을 업무 테이블에(기존 데이터 유지)")
    rp.add_argument("ids", type=int, nargs="+")
    rp.add_argument("--apply", action="store_true", help="실제로 넣는다(없으면 계획만)")
    for name in ("hold", "discard"):
        p = sub.add_parser(name)
        p.add_argument("id", type=int)
        p.add_argument("--note")
    pr = sub.add_parser("prune", help="오래된 기록 정리")
    pr.add_argument("--keep-days", type=int, default=180)
    pr.add_argument("--include-open", action="store_true", help="미해결 격리도 지운다(기본은 끝난 기록·경고만)")
    pr.add_argument("--dry-run", action="store_true")
    return ap


def main(argv: list[str] | None = None, session_factory=None) -> int:
    args = build_parser().parse_args(argv)
    from app import quarantine as Q

    if session_factory is None:
        from app.db.session import SessionLocal as session_factory
    with __import__("contextlib").suppress(Exception):
        sys.stdout.reconfigure(errors="replace")
    db = session_factory()
    try:
        if args.cmd == "summary":
            s = Q.open_summary(db)
            if not s:
                print("미해결 격리 없음")
            for src, v in sorted(s.items()):
                print(f"{src}: {v['open_quarantined']}건 " + ", ".join(f"{k} {n}" for k, n in sorted(v["by_code"].items()))
                      + f" (마지막 {v['last_seen_at']})")
            return 0
        if args.cmd == "list":
            items = Q.list_items(db, args.source, args.code, args.status, "warning" if args.warnings else "quarantined", args.limit)
            print(f"{'id':>6} {'source':<13} {'validation':<11} {'resolution':<13} {'error_code':<26} 횟수 마지막          key")
            for it in items:
                print(_row(it))
            print(f"({len(items)}건)")
            return 0
        if args.cmd == "show":
            it = Q.get(db, args.id)
            if it is None:
                print(f"id {args.id} 없음")
                return 1
            print(_row(it))
            print(f"사유: {it.error_detail}")
            print(f"처리: {it.resolution_status} {it.resolution_note or ''} · 재검증 {it.retry_count}회 · run {it.run_id} {it.step or ''}")
            print(f"payload{'(잘림: 재처리 불가)' if it.payload_truncated else ''}:")
            print(json.dumps(it.payload, ensure_ascii=False, indent=1)[:4000])
            return 0
        if args.cmd == "revalidate":
            ids = list(args.ids)
            if args.all_open:
                ids += [it.id for it in Q.list_items(db, args.source, None, "open", "quarantined", 10000)]
            if not ids:
                print("다시 판정할 대상이 없습니다(ID 또는 --all-open)")
                return 1
            for i in ids:
                print(f"{i}: {Q.revalidate(db, i)}")
            return 0
        if args.cmd == "reprocess":
            bad = 0
            for i in args.ids:
                r = Q.reprocess(db, i, apply=args.apply)
                bad += r.startswith(("failed", "not_found"))
                print(f"{i}: {r}" + ("" if args.apply else " (계획만: --apply로 실행)"))
            return 1 if bad else 0
        if args.cmd in ("hold", "discard"):
            r = (Q.hold if args.cmd == "hold" else Q.discard)(db, args.id, args.note)
            print(f"{args.id}: {r}")
            return 1 if r == "not_found" else 0
        if args.cmd == "prune":
            try:
                n = Q.prune(db, args.keep_days, args.dry_run, args.include_open)
            except ValueError as e:
                print(str(e))
                return 2
            print(f"{'지울 예정' if args.dry_run else '지움'}: {n}건 (마지막 확인 {args.keep_days}일 이전"
                  + (", 미해결 포함" if args.include_open else ", 끝난 기록·경고만") + ")")
            return 0
    finally:
        db.close()
    return 2


if __name__ == "__main__":
    sys.exit(main())
