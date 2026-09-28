"""시가총액 상위 N개 (ticker, name) 리스트 생성.

데이터 소스: FinanceDataReader의 KRX 상장 종목 목록(로그인 불필요, Marcap 포함).
DART corpCode.xml에 stock_code가 없는 종목(우선주, 일부 SPAC 등)은 공시 수집이 불가능하므로
제외 목록으로 따로 남긴다. 상위 N개는 DART 대상 종목만으로 채운다.

사용: python -m scripts.build_universe [N]   -> scripts/universe_top{N}.json
"""

import json
import sys
from pathlib import Path

import FinanceDataReader as fdr

from app.collectors import get_dart_corp_code

OUT_DIR = Path(__file__).resolve().parent


def build_universe(n: int = 300) -> dict:
    df = fdr.StockListing("KRX")
    df = df[df["Market"] != "KONEX"].sort_values("Marcap", ascending=False)

    universe, excluded = [], []
    for _, row in df.iterrows():
        if len(universe) >= n:
            break
        entry = {
            "rank": len(universe) + len(excluded) + 1,
            "ticker": row["Code"],
            "name": row["Name"],
            "market": row["Market"],
            "marcap": int(row["Marcap"]),
        }
        corp_code = get_dart_corp_code(row["Code"])
        if corp_code is None:
            excluded.append(entry)
        else:
            universe.append({**entry, "corp_code": corp_code})

    return {"universe": universe, "excluded_not_in_dart": excluded}


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    result = build_universe(n)
    out = OUT_DIR / f"universe_top{n}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"대상 {len(result['universe'])}개, DART 미등록 제외 {len(result['excluded_not_in_dart'])}개 -> {out}")
