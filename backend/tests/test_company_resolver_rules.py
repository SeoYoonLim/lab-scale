"""기본 모드 종목 해석 규칙(2026-10-27): 한글 음차, 앞부분 약칭, 자모 한 글자 오타. 오매칭 0을 지키는지 적대적 입력으로 본다.

순수 테스트는 실제 dev DB 종목명 일부로 만든 FakeDB를, integration 테스트는 실제 company 테이블 전체를 쓴다.
"""

import pytest

from app.tools.company_resolver import _one_jamo_apart, resolve_company, transliterate_prefix

# 실제 dev DB에 있는 이름들(헷갈리기 쉬운 것 위주)
NAMES = [
    "삼성전자", "삼성전기", "삼성SDI", "삼성에스디에스", "삼성생명", "삼성화재", "삼성중공업", "삼성바이오로직스",
    "KB금융", "JB금융지주", "BNK금융지주", "신한지주", "하나금융지주", "우리금융지주", "한국금융지주",
    "현대차", "현대모비스", "현대건설", "현대제철", "현대해상", "현대글로비스", "현대무벡스", "현대엘리베이터",
    "HD현대", "HD현대중공업", "HD현대마린엔진", "HD현대마린솔루션", "HD현대일렉트릭",
    "LG", "LG화학", "LG전자", "LG에너지솔루션", "LG디스플레이", "LG생활건강", "LG이노텍",
    "SK", "SK하이닉스", "SK텔레콤", "SK이노베이션", "SK바이오팜", "SK바이오사이언스", "SK스퀘어", "SKC",
    "GS", "GS건설", "GS리테일", "CJ", "CJ제일제당", "CJ대한통운", "DB하이텍", "DB손해보험", "NH투자증권",
    "셀트리온", "셀트리온제약", "카카오", "카카오뱅크", "카카오페이", "에코프로", "에코프로비엠", "에코프로머티",
    "한미반도체", "한미약품", "한미사이언스", "한국전력", "한국가스공사", "대한항공", "대한전선", "대한조선",
    "미래에셋생명", "미래에셋증권", "동서", "두산", "두산로보틱스", "레인보우로보틱스", "롯데쇼핑", "롯데지주", "롯데웰푸드",
    "한화에어로스페이스", "한화생명", "기아", "KT", "KT&G",
]

# (입력, 기본 모드 기대 종목 또는 None). None = 어떤 종목도 돌려주면 안 됨(못 찾음이나 후보 안내).
CASES = [
    # 한글 음차
    ("엘지화학", "LG화학"), ("케이비금융", "KB금융"), ("에스케이텔레콤", "SK텔레콤"), ("지에스건설", "GS건설"),
    ("씨제이제일제당", "CJ제일제당"), ("엘지에너지솔루션", "LG에너지솔루션"), ("디비하이텍", "DB하이텍"),
    ("엔에이치투자증권", "NH투자증권"), ("케이티앤지", "KT&G"), ("에스케이씨", "SKC"),
    # 앞부분 약칭(유일할 때만)
    ("JB금융", "JB금융지주"), ("한미반도", "한미반도체"), ("카카오뱅", "카카오뱅크"), ("현대모비", "현대모비스"),
    ("셀트리온제", "셀트리온제약"), ("LG디스", "LG디스플레이"), ("두산로보", "두산로보틱스"), ("엘지에너지", "LG에너지솔루션"),
    # 자모 한 글자 오타
    ("셀트리언", "셀트리온"), ("현대모비수", "현대모비스"), ("한국전략", "한국전력"), ("대한항궁", "대한항공"),
    # 정확 일치는 비슷한 이름에 끌려가지 않는다
    ("삼성SDI", "삼성SDI"), ("삼성SDS", "삼성에스디에스"), ("KB금융", "KB금융"), ("현대차", "현대차"), ("에코프로", "에코프로"),
    ("카카오", "카카오"), ("SK", "SK"), ("LG", "LG"), ("GS", "GS"), ("CJ", "CJ"), ("KT", "KT"), ("셀트리온", "셀트리온"),
    # 어떤 종목도 고르면 안 되는 입력
    ("현대", None), ("삼성전자우", None), ("롯데칠성", None), ("SK바이오", None), ("미래에셋", None), ("HD현대마린", None),
    ("동성", None), ("대한제당", None), ("삼성제약", None), ("신한금융투자", None), ("한미글로벌", None), ("엘지", "LG"),
    ("케이비", None), ("삼성전", None), ("SK하이닉스우", None), ("현대차2우B", None), ("JB", None), ("롯데", None),
]


@pytest.fixture
def db(make_db):
    return make_db(*NAMES)


def test_case_count_is_adversarial_enough():
    assert len(CASES) >= 40
    assert sum(1 for _, e in CASES if e is None) >= 15


@pytest.mark.parametrize("raw, expected", CASES)
def test_default_mode(db, raw, expected):
    res = resolve_company(db, raw)
    got = res.company.name if res.company else None
    assert got == expected


@pytest.mark.parametrize("raw, expected", [c for c in CASES if c[1] is not None])
def test_strict_mode_does_not_use_new_rules(db, raw, expected):
    """엄격 모드: 정확 일치·공백 무시·별칭 외에는 종목을 돌려주지 않는다(새 규칙 불사용)."""
    res = resolve_company(db, raw, allow_fuzzy=False)
    exact_or_alias = raw in NAMES or raw == "삼성SDS"
    # 엄격 모드도 공백·대소문자 무시와 별칭은 인정한다(새 규칙이 아님). 음차·약칭·자모 오타는 인정하지 않는다.
    if exact_or_alias:
        assert res.company.name == expected
    else:
        assert res.company is None


def test_ambiguous_prefix_lists_candidates(db):
    res = resolve_company(db, "SK바이오")
    assert res.company is None and set(res.candidates) == {"SK바이오팜", "SK바이오사이언스"}


def test_ambiguous_jamo_lists_candidates(make_db):
    res = resolve_company(make_db("가나다라", "가나다마", "나다"), "가나다바")  # 라/마/바: 초성만 다르다(둘 다 한 자모 차이)
    assert res.company is None and set(res.candidates) == {"가나다라", "가나다마"}


def test_transliterate_prefix():
    assert transliterate_prefix("엘지화학") == "LG화학"
    assert transliterate_prefix("에스케이씨") == "SKC"  # 긴 것 먼저
    assert transliterate_prefix("엘지") == "LG"  # 이름 전체가 약어인 종목("LG", "KT")도 있다
    assert transliterate_prefix("삼성전자") == "삼성전자"


@pytest.mark.parametrize("a, b, expected", [
    ("셀트리언", "셀트리온", True), ("한국전략", "한국전력", True), ("현대모비수", "현대모비스", True),
    ("동성", "동서", False),  # 2글자
    ("대한제당", "대한전선", False),  # 두 글자 다름
    ("삼성전자", "삼성전자", False),
    ("셀트리언제약", "셀트리온", False),  # 길이 다름
    ("가나다라", "가나다랑", True),  # 종성 하나 추가
    ("가나다a", "가나다b", False),  # 한글이 아닌 글자 차이
])
def test_one_jamo_apart(a, b, expected):
    assert _one_jamo_apart(a.casefold(), b.casefold()) is expected


@pytest.mark.integration
@pytest.mark.parametrize("raw, expected", CASES)
def test_no_wrong_match_on_real_company_table(dev_db, raw, expected):
    """실제 company 테이블 전체: 기대 종목이거나, 기대가 None이면 아무 종목도 돌려주지 않아야 한다(오매칭 0)."""
    res = resolve_company(dev_db, raw)
    got = res.company.name if res.company else None
    assert got == expected


@pytest.mark.integration
def test_no_two_real_names_are_one_jamo_apart_without_notice(dev_db):
    """실제 종목명 중 한 자모 차이 쌍이 있으면, 그 사이의 오타 입력은 후보 안내로 끝나야 한다(자동 채택 금지)."""
    from app.models import Company
    from app.tools.company_resolver import _key

    names = [n for (n,) in dev_db.query(Company.name)]
    keys = {_key(n): n for n in names}
    for k, n in keys.items():
        twins = [m for m in keys if _one_jamo_apart(k, m)]
        if twins:
            assert resolve_company(dev_db, n).company.name == n  # 정확 일치가 항상 먼저다


def test_korean_brand_is_not_transliterated(make_db):
    """'포스코'는 약어 음차가 아니라 여러 종목에 걸치는 한글 브랜드다. 음차로 POSCO홀딩스 하나에 붙이면 안 된다."""
    db = make_db("POSCO홀딩스", "포스코퓨처엠", "포스코인터내셔널")
    assert resolve_company(db, "포스코").company is None
    assert resolve_company(db, "포스코퓨처").company.name == "포스코퓨처엠"  # 한글 이름의 앞부분 약칭은 된다
