"""DART 공시 원문을 수집해 disclosure.content를 채운다 (app/dart_documents.py 참고).

기본은 dry-run(호출은 하지만 DB에 쓰지 않는다). 중간에 끊겨도 같은 명령을 다시 실행하면 content가
비어 있는 행부터 이어서 한다.

    python scripts/collect_disclosure_content.py --limit 10            # 파일럿(dry-run)
    python scripts/collect_disclosure_content.py --apply                # 전체 수집
    python scripts/collect_disclosure_content.py --apply --retry-failed # 원문 없음으로 기록된 건도 재시도
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.dart_documents import DEFAULT_DELAY, collect_disclosure_content  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="DB에 저장한다(기본은 dry-run)")
    p.add_argument("--limit", type=int, default=None, help="이번 실행에서 처리할 최대 건수")
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="요청 간 대기(초)")
    p.add_argument("--retry-failed", action="store_true", help="원문 없음으로 기록된 건도 다시 시도")
    a = p.parse_args()

    s = collect_disclosure_content(limit=a.limit, delay=a.delay, dry_run=not a.apply, retry_failed=a.retry_failed)
    print(
        f"\n=== 요약 ({'저장' if a.apply else 'dry-run'}) ===\n"
        f"대상 {s.targets}건 / API 호출 {s.calls}회 / 소요 {s.elapsed_s:.0f}초\n"
        f"성공 {s.ok} / 원문없음 {s.no_file} / 빈본문 {s.empty} / 일시실패(다음 실행에서 재시도) {s.transient}"
        f" / 이전 실패로 건너뜀 {s.skipped_known_failures}"
        + ("\n※ DART 요청 제한에 걸려 중단됨" if s.quota_hit else "")
    )
    for id_, title, text in s.samples:
        print(f"  샘플 id={id_} {title[:20]!r}: {text!r}")


if __name__ == "__main__":
    main()
