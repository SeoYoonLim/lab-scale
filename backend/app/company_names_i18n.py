"""국내 종목의 영어·일본어·중국어 표기 -> company 테이블 등록명(48차, 2026-11-13).

배경: 질문은 여러 언어로 들어온다(locale은 답변 언어일 뿐이다). "サムスン電子", "三星电子", "Samsung Electronics"는 모두 삼성전자인데
company.name은 한글 등록명만 있어서 extract_from_text·resolve_company가 찾지 못했다. LLM이 그 표기를 그대로 도구 인자로 넘기면
퍼지 매칭은 실패하거나 다른 종목으로 잘못 연결할 수 있다(48차 실측: "KODEX 200にサムスン電子は…"에서 삼성전자를 찾지 못함).

규칙(app/company_aliases.py와 같다):
- 값은 company.name과 글자 그대로 같다(tests/test_company_names_i18n.py가 등록 종목 목록·dev DB로 확인).
- 회사·언론이 실제로 쓰는 표기만 넣는다. 확신이 없는 표기(중국어 음차가 여러 가지인 회사 등)는 넣지 않는다.
- 두 회사 이상에 걸치거나 일반 낱말과 겹치는 표기는 넣지 않는다:
  "Samsung"/"三星"/"サムスン"(삼성 계열 여러 종목), "Hyundai"/"現代"/"现代", "LG", "SK", "Lotte",
  "カカオ"(일본어로 코코아), "Orion"(오리온: 별자리), "新世界"(신세계: 중국어·일본어 일반 낱말), "ハイブ"(ハイブリッド의 앞부분).
- 영문 표기는 extract_from_text에서 앞뒤가 영문·숫자가 아닐 때만 인정된다("Kia"가 "Kiaora"에 걸리지 않는다).
  일본어·중국어 표기는 경계 검사가 없으므로 다른 낱말의 일부가 되기 쉬운 짧은 표기("キア")는 넣지 않는다.
- 공백·대소문자는 무시하고 비교한다("SK Hynix" == "sk hynix" == "SKHynix"). 다만 질문에서 찾을 때(extract_from_text)는 공백까지
  그대로 비교하므로 띄어 쓰는 표기와 붙여 쓰는 표기("Kakao Bank"·"KakaoBank")를 둘 다 넣는다.
"""

# 등록명 -> (영어, 일본어, 중국어 간체·번체) 표기. 한 종목의 표기를 한 줄에 모은다.
FOREIGN_NAMES: dict[str, tuple[str, ...]] = {
    "삼성전자": ("Samsung Electronics", "サムスン電子", "三星電子", "三星电子"),
    "SK하이닉스": ("SK hynix", "SKハイニックス", "SK海力士"),
    "SK스퀘어": ("SK Square", "SKスクエア"),
    "삼성전기": ("Samsung Electro-Mechanics", "サムスン電機", "三星電機", "三星电机"),
    "LG에너지솔루션": ("LG Energy Solution", "LGエナジーソリューション", "LG新能源"),
    "현대차": ("Hyundai Motor", "Hyundai Motors", "Hyundai Motor Company", "現代自動車", "现代汽车", "現代汽車"),
    "삼성바이오로직스": ("Samsung Biologics", "サムスンバイオロジクス", "三星生物制剂", "三星生物製劑"),
    "KB금융": ("KB Financial", "KB Financial Group", "KBフィナンシャルグループ", "KB金融", "KB金融集团"),
    "삼성물산": ("Samsung C&T", "サムスン物産", "三星物产", "三星物產"),
    "삼성생명": ("Samsung Life Insurance", "Samsung Life", "サムスン生命", "三星生命"),
    "한화에어로스페이스": ("Hanwha Aerospace", "ハンファエアロスペース", "韩华航空航天"),
    "두산에너빌리티": ("Doosan Enerbility", "斗山エナビリティ"),
    "신한지주": ("Shinhan Financial Group", "Shinhan Financial", "新韓フィナンシャルグループ", "新韩金融", "新韩金融集团"),
    "HD현대중공업": ("HD Hyundai Heavy Industries", "HD現代重工業", "HD现代重工"),
    "기아": ("Kia", "Kia Motors", "Kia Corporation", "起亜", "起亚", "起亞"),
    "삼성SDI": ("Samsung SDI", "サムスンSDI", "三星SDI"),
    "셀트리온": ("Celltrion", "セルトリオン"),
    "하나금융지주": ("Hana Financial Group", "Hana Financial", "ハナ金融グループ", "韩亚金融", "韩亚金融集团"),
    "LG전자": ("LG Electronics", "LG電子", "LG电子"),
    "현대모비스": ("Hyundai Mobis", "現代モービス", "现代摩比斯"),
    "삼성화재": ("Samsung Fire & Marine Insurance", "Samsung Fire & Marine", "サムスン火災", "三星火灾", "三星火災"),
    "우리금융지주": ("Woori Financial Group", "Woori Financial", "ウリィ金融グループ", "友利金融", "友利金融集团"),
    "SK이노베이션": ("SK Innovation", "SKイノベーション", "SK创新"),
    "POSCO홀딩스": ("POSCO Holdings", "ポスコホールディングス", "浦项控股"),
    "한화오션": ("Hanwha Ocean", "ハンファオーシャン", "韩华海洋"),
    "고려아연": ("Korea Zinc", "高麗亜鉛"),
    "한국전력": ("Korea Electric Power", "KEPCO", "韓国電力", "韩国电力", "韓國電力"),
    "한전기술": ("KEPCO E&C", "KEPCO Engineering & Construction"),
    "한전KPS": ("KEPCO KPS",),
    "SK텔레콤": ("SK Telecom", "SKテレコム", "SK电讯", "SK電訊"),
    "LG화학": ("LG Chem", "LG化学"),
    "삼성중공업": ("Samsung Heavy Industries", "サムスン重工業", "三星重工"),
    "현대글로비스": ("Hyundai Glovis", "現代グロービス"),
    "현대건설": ("Hyundai Engineering & Construction", "Hyundai E&C", "現代建設", "现代建设"),
    "대한항공": ("Korean Air", "大韓航空", "大韩航空"),
    "크래프톤": ("Krafton", "クラフトン"),
    "아모레퍼시픽": ("Amorepacific", "アモーレパシフィック", "爱茉莉太平洋"),
    "카카오": ("Kakao",),
    "카카오뱅크": ("KakaoBank", "Kakao Bank", "カカオバンク"),
    "카카오페이": ("Kakao Pay", "KakaoPay", "カカオペイ"),
    "넷마블": ("Netmarble", "ネットマーブル"),
    "NC": ("NCSoft", "エヌシーソフト"),
    "현대제철": ("Hyundai Steel", "現代製鉄", "现代制铁"),
    "LG디스플레이": ("LG Display", "LGディスプレイ", "LG显示"),
    "롯데쇼핑": ("Lotte Shopping", "ロッテショッピング"),
    "롯데케미칼": ("Lotte Chemical", "ロッテケミカル"),
    "CJ제일제당": ("CJ CheilJedang", "CJ第一製糖", "CJ第一制糖"),
    "기업은행": ("Industrial Bank of Korea",),
    "미래에셋증권": ("Mirae Asset Securities", "未来アセット証券", "未来资产证券"),
    "삼성증권": ("Samsung Securities", "サムスン証券", "三星证券"),
    "삼성카드": ("Samsung Card", "サムスンカード"),
    "삼성에스디에스": ("サムスンSDS", "三星SDS"),  # 영문 "Samsung SDS"·"삼성SDS"는 app/company_aliases.py에 있다
    "한국항공우주": ("Korea Aerospace Industries", "韓国航空宇宙産業"),
    "에코프로": ("EcoPro", "エコプロ"),
    "에코프로비엠": ("EcoPro BM", "エコプロBM"),
    "알테오젠": ("Alteogen",),
    "한미반도체": ("Hanmi Semiconductor", "ハンミ半導体"),
    "유한양행": ("Yuhan", "Yuhan Corporation"),
    "한미약품": ("Hanmi Pharmaceutical", "Hanmi Pharm"),
    "강원랜드": ("Kangwon Land",),
    "코웨이": ("Coway",),
    "두산밥캣": ("Doosan Bobcat",),
    "현대로템": ("Hyundai Rotem", "現代ロテム"),
    "한화솔루션": ("Hanwha Solutions", "ハンファソリューションズ"),
    "LG유플러스": ("LG Uplus",),
    "효성중공업": ("Hyosung Heavy Industries",),
    "HD한국조선해양": ("HD Korea Shipbuilding & Offshore Engineering", "HD KSOE"),
    "HD현대일렉트릭": ("HD Hyundai Electric",),
    "메리츠금융지주": ("Meritz Financial Group",),
    "현대오토에버": ("Hyundai AutoEver",),
    "한화시스템": ("Hanwha Systems",),
    "포스코퓨처엠": ("POSCO Future M",),
    "포스코인터내셔널": ("POSCO International",),
    "삼양식품": ("Samyang Foods", "三養食品", "三养食品"),
    "LG이노텍": ("LG Innotek", "LGイノテック"),
    "대우건설": ("Daewoo Engineering & Construction", "Daewoo E&C"),
    "키움증권": ("Kiwoom Securities",),
    "LG생활건강": ("LG H&H", "LG Household & Health Care"),
    "삼성에피스홀딩스": ("Samsung Epis Holdings",),
    "SK바이오팜": ("SK Biopharm", "SK Biopharmaceuticals"),
    "농심": ("Nongshim", "農心", "农心"),
    "이마트": ("E-Mart", "Emart"),
    "한화생명": ("Hanwha Life", "Hanwha Life Insurance"),
    "현대해상": ("Hyundai Marine & Fire Insurance",),
    "DB손해보험": ("DB Insurance",),
    "한국가스공사": ("Korea Gas Corporation", "KOGAS"),
}


def foreign_aliases() -> dict[str, str]:
    """표기 -> 등록명(평평한 사전). 같은 표기가 두 종목에 걸리면 ValueError(사전 오류를 조용히 덮어쓰지 않는다)."""
    out: dict[str, str] = {}
    for name, aliases in FOREIGN_NAMES.items():
        for a in aliases:
            if a in out and out[a] != name:
                raise ValueError(f"외국어 표기 {a!r}가 {out[a]}와 {name}에 함께 걸린다")
            out[a] = name
    return out
