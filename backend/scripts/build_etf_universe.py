"""시가총액 상위 N개 ETF (ticker, name) 리스트 생성.

데이터 소스: FinanceDataReader의 ETF 목록(StockListing("ETF/KR"), 로그인 불필요). 시가총액(MarCap, 단위: 억원) 내림차순으로 고른다.
주식 유니버스(build_universe)와 달리 DART 대상 여부는 보지 않는다(ETF는 공시 수집을 하지 않는다).

시가총액 순위와 상관없이 반드시 넣는 대표 ETF(MUST_INCLUDE)가 있다. 그중 상위 N개 안에 못 든 것이 있으면, 상위 N개의
맨 끝(시가총액이 가장 작은 것)부터 빼서 총 N개를 맞춘다. 목록에 그 이름이 없으면 같은 티커, 그다음 가장 비슷한 이름의 ETF로
대체하고 그 사실을 출력한다.

출력 형식은 universe_top{N}.json과 같다(`universe`, `excluded_not_in_dart`). 항목: rank(전체 ETF 중 시가총액 순위),
ticker, name, market(ETF는 없어서 null), marcap(억원), asset_type("etf"), must_include(대표 ETF로 넣은 것이면 true).
universe 파일은 커밋 대상이 아니다(.gitignore).

사용: python -m scripts.build_etf_universe [N]   -> scripts/universe_etf{N}.json
"""

import difflib
import json
import sys
from pathlib import Path

import FinanceDataReader as fdr

from app import http_retry

OUT_DIR = Path(__file__).resolve().parent

# (티커, 이름). 이름이 목록에 없으면 티커로, 그것도 없으면 가장 비슷한 이름으로 대체한다.
MUST_INCLUDE = [
    ("069500", "KODEX 200"),
    ("360750", "TIGER 미국S&P500"),
    ("133690", "TIGER 미국나스닥100"),
    ("122630", "KODEX 레버리지"),
    ("114800", "KODEX 인버스"),
]


def _find_must(listing, ticker: str, name: str) -> tuple[int | None, str | None]:
    """(listing의 행 위치, 대체 사유). 이름이 일치하면 사유는 None, 못 찾으면 위치도 None."""
    by_name = listing.index[listing["Name"] == name]
    if len(by_name):
        return listing.index.get_loc(by_name[0]), None
    by_code = listing.index[listing["Symbol"] == ticker]
    if len(by_code):
        i = listing.index.get_loc(by_code[0])
        return i, f"이름 '{name}'이 목록에 없어 같은 티커 {ticker}('{listing.iloc[i]['Name']}')로 대체"
    close = difflib.get_close_matches(name, listing["Name"].tolist(), n=1, cutoff=0.6)
    if close:
        i = listing.index.get_loc(listing.index[listing["Name"] == close[0]][0])
        return i, f"'{name}'이 목록에 없어 가장 비슷한 '{close[0]}'({listing.iloc[i]['Symbol']})로 대체"
    return None, f"'{name}'을 목록에서 찾지 못해 제외"


def build_etf_universe(n: int = 50) -> tuple[dict, list[str]]:
    """({"universe", "excluded_not_in_dart"}, 대체/제외 안내 메시지 목록)."""
    listing = http_retry.call_with_retry(lambda: http_retry.with_timeout(lambda: fdr.StockListing("ETF/KR")),
                                         label="FDR StockListing")
    listing = listing.sort_values("MarCap", ascending=False).reset_index(drop=True)
    notes: list[str] = []

    def entry(i: int, must: bool) -> dict:
        row = listing.iloc[i]
        out = {
            "rank": i + 1,
            "ticker": row["Symbol"],
            "name": row["Name"],
            "market": None,
            "marcap": int(row["MarCap"]),
            "asset_type": "etf",
        }
        if must:
            out["must_include"] = True
        return out

    must_pos: list[int] = []
    for ticker, name in MUST_INCLUDE:
        i, note = _find_must(listing, ticker, name)
        if note:
            notes.append(note)
        if i is not None and i not in must_pos:
            must_pos.append(i)

    top = list(range(min(n, len(listing))))
    missing = [i for i in must_pos if i not in top]
    keep = top[: max(0, n - len(missing))] if missing else top
    chosen = sorted(set(keep) | set(must_pos))
    dropped = [i for i in top if i not in chosen]
    if dropped:
        notes.append(
            "대표 ETF를 넣으려고 시가총액 하위에서 뺀 ETF: "
            + ", ".join(f"{listing.iloc[i]['Name']}({i + 1}위)" for i in dropped)
        )
    for i in missing:
        notes.append(f"대표 ETF '{listing.iloc[i]['Name']}'는 시가총액 {i + 1}위라 상위 {n}개 밖이지만 포함")

    universe = [entry(i, i in must_pos) for i in chosen]
    return {"universe": universe, "excluded_not_in_dart": []}, notes


def main(argv: list[str] | None = None) -> Path:
    args = sys.argv[1:] if argv is None else argv
    n = int(args[0]) if args else 50
    result, notes = build_etf_universe(n)
    out = OUT_DIR / f"universe_etf{n}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    for note in notes:
        print(note)
    print(f"대상 {len(result['universe'])}개 -> {out}")
    return out


if __name__ == "__main__":
    main()
