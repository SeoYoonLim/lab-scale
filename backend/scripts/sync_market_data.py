"""클라우드 시장 데이터 패키지를 로컬 DB에 동기화한다(2026-11-23, app/market_data_sync). 서버를 켜면 자동으로도 돈다.

사용 예:
  python -m scripts.sync_market_data --status            # 받을 수 있는 패키지·적용 기록만(DB 쓰기 없음)
  python -m scripts.sync_market_data --dry-run           # 검증하고 넣을 행 수만 센다(DB 쓰기 없음)
  python -m scripts.sync_market_data                     # 적용(없는 행만 넣음)
  python -m scripts.sync_market_data --dir out/ --no-fetch   # git 대신 폴더(시험용)
  python -m scripts.sync_market_data --init-key          # 키 파일(~/.lab-scale/market_data_key) 만들기(이미 있으면 멈춤). 키는 출력하지 않는다
종료 코드: 0 성공·최신 / 1 일부 실패 / 2 키 없음·오류 / 4 다른 갱신 실행 중
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app import market_data_sync as S
from app import market_package as P


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--reapply", action="store_true", help="적용 기록이 있는 패키지도 다시(없는 행만 넣으므로 안전)")
    ap.add_argument("--dir", default=None, help="market-data 브랜치 대신 이 폴더")
    ap.add_argument("--no-fetch", action="store_true", help="git fetch를 하지 않는다")
    ap.add_argument("--init-key", action="store_true")
    args = ap.parse_args(argv)

    if args.init_key:
        path = P.DEFAULT_KEY_FILE
        if path.exists():
            print(f"키 파일이 이미 있다: {path} (덮어쓰지 않음)")
            return 2
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(P.new_key_text(), encoding="ascii")
        print(f"키 파일을 만들었다: {path} (key_id {P.key_id(P.load_key({}) or b'')}). 같은 값을 GitHub 저장소 Secret MARKET_DATA_KEY에 넣는다.")
        return 0

    source = S.DirSource(Path(args.dir)) if args.dir else S.GitSource()
    if args.status:
        key = P.load_key()
        if not args.no_fetch:
            source.fetch()
        manifests = source.list_manifests()
        state = S.load_state()
        print(json.dumps({"key": f"있음(key_id {P.key_id(key)})" if key else "없음", "available": len(manifests),
                          "applied": sum(1 for m in manifests if m in state.get("applied", {})),
                          "pending": [m for m in manifests if m not in state.get("applied", {})][-10:],
                          "latest": manifests[-1] if manifests else None}, ensure_ascii=False, indent=1))
        return 0
    r = S.sync(source=source, dry_run=args.dry_run, reapply=args.reapply, fetch=not args.no_fetch)
    print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
    return {"ok": 0, "up_to_date": 0, "partial": 1, "locked": 4}.get(r.get("status"), 2)


if __name__ == "__main__":
    sys.exit(main())
