"""국내 종목의 DART 공식 영문 회사명 별칭(49차, 2026-11-13). app/company_names_i18n.FOREIGN_NAMES와 함께 별칭 단계에 들어간다.

출처: 금융감독원 DART 고유번호 파일(corpCode.xml)의 corp_eng_name, 2026-11-13 조회(유니버스 상위 300 주식).
만든 규칙(검증 근거, docs/LLM_ANSWER_QUALITY_49.md 5절):
- 법인 접미어(Co., Ltd. / Corporation / Inc. 등)만 지웠다. 붙여 쓴 이름(KoreaGas, PearlAbyss)은 띄어 쓴 표기도 넣었다.
- 다른 종목의 등록명·기존 별칭(한글·외국어)과 정규화(공백·대소문자 무시)가 같으면 넣지 않았다.
- 한 낱말이고 일반 낱말(Sphere)이거나 그룹명이라 여러 종목에 걸치면(DOOSAN, HANWHA, HYOSUNG, Lotte, Youngone) 넣지 않았다.
  3자 이하 약어(APR, PSK, TSE, SL, VM …)도 넣지 않았다(다른 회사·낱말과 겹친다).
- 일본어·중국어 표기는 공식 출처로 확인되지 않아 이번에 늘리지 않았다(48차 86종목 그대로).
"""

DART_ENGLISH_NAMES: dict[str, tuple[str, ...]] = {
    "삼성에스디에스": ("SAMSUNG SDS",),  # 45위, DART: SAMSUNG SDS CO., LTD.
    "HD현대": ("HD HYUNDAI",),  # 48위, DART: HD HYUNDAI CO.,LTD.
    "LIG디펜스앤에어로스페이스": ("LIG Defense&Aerospace",),  # 50위, DART: LIG Defense&Aerospace Co., Ltd.
    "현대로템": ("Hyundai-Rotem",),  # 58위, DART: Hyundai-Rotem Co.
    "대한항공": ("KOREAN AIR LINES",),  # 61위, DART: KOREAN AIR LINES CO.,LTD
    "HD현대마린솔루션": ("HD Hyundai Marine Solution",),  # 63위, DART: HD Hyundai Marine Solution Co., Ltd
    "한국금융지주": ("Korea Investment Holdings",),  # 64위, DART: Korea Investment Holdings Co., Ltd
    "GS": ("GS Holdings",),  # 66위, DART: GS Holdings
    "주성엔지니어링": ("JUSUNG ENGINEERING",),  # 69위, DART: JUSUNG ENGINEERING Co.,Ltd.
    "삼성E&A": ("SAMSUNG E&A",),  # 71위, DART: SAMSUNG E&A CO.,LTD
    "NH투자증권": ("NH INVESTMENT & SECURITIES",),  # 72위, DART: NH INVESTMENT & SECURITIES CO.,LTD.
    "가온전선": ("GAON CABLE",),  # 74위, DART: GAON CABLE CO.,LTD
    "한진칼": ("HANJINKAL", "Hanjin KAL",),  # 75위, DART: HANJINKAL
    "이수페타시스": ("ISUPETASYS", "Isu Petasys",),  # 78위, DART: ISUPETASYS CO.,LTD
    "레인보우로보틱스": ("Rainbow Robotics",),  # 79위, DART: Rainbow Robotics
    "한국타이어앤테크놀로지": ("HANKOOK TIRE & TECHNOLOGY",),  # 84위, DART: HANKOOK TIRE & TECHNOLOGY CO.,LTD
    "원익IPS": ("WONIK IPS",),  # 92위, DART: WONIK IPS CO., LTD.
    "산일전기": ("SANIL ELECTRIC",),  # 93위, DART: SANIL ELECTRIC CO., LTD
    "HD건설기계": ("HD Construction Equipment",),  # 95위, DART: HD Construction Equipment Co.,Ltd.
    "대한전선": ("Taihan Cable & Solution",),  # 96위, DART: Taihan Cable & Solution Co., Ltd.
    "이오테크닉스": ("EO Technics",),  # 98위, DART: EO Technics CO.,LTD
    "대덕전자": ("DAEDUCK ELECTRONICS",),  # 99위, DART: DAEDUCK ELECTRONICS CO.,LTD
    "심텍": ("SIMMTECH",),  # 100위, DART: SIMMTECH Co., Ltd.
    "DB하이텍": ("DB HiTek",),  # 101위, DART: DB HiTek Co.,LTD
    "리노공업": ("LEENO Industrial",),  # 102위, DART: LEENO Industrial Inc
    "JB금융지주": ("JB FINANCIAL GROUP",),  # 103위, DART: JB FINANCIAL GROUP CO.,LTD
    "BNK금융지주": ("BNK Financial Group",),  # 109위, DART: BNK Financial Group Inc.
    "맥쿼리인프라": ("Macquarie Korea Infrastructure Fund",),  # 111위, DART: Macquarie Korea Infrastructure Fund
    "두산로보틱스": ("Doosan Robotics",),  # 112위, DART: Doosan Robotics Inc.
    "로보티즈": ("ROBOTIS",),  # 118위, DART: ROBOTIS
    "파두": ("FADU",),  # 125위, DART: FADU INC.
    "현대해상": ("HYUNDAIMARINE&FIREINSURANCECO",),  # 127위, DART: HYUNDAIMARINE&FIREINSURANCECO.,LTD.
    "한화엔진": ("Hanwha Engine",),  # 128위, DART: Hanwha Engine Co., Ltd.
    "일진전기": ("ILJIN ELECTRIC",),  # 129위, DART: ILJIN ELECTRIC CO.,LTD
    "삼천당제약": ("SAM CHUN DANG PHARM",),  # 130위, DART: SAM CHUN DANG PHARM CO. LTD
    "OCI홀딩스": ("OCI Holdings",),  # 131위, DART: OCI Holdings Company Ltd.
    "유진테크": ("EugeneTechnologyCo", "Eugene Technology Co",),  # 133위, DART: EugeneTechnologyCo.,Ltd.
    "한미사이언스": ("Hanmi Science",),  # 134위, DART: Hanmi Science Co.,Ltd
    "피에스케이홀딩스": ("PSK HOLDINGS",),  # 135위, DART: PSK HOLDINGS INC.
    "한국콜마": ("KOLMAR KOREA",),  # 136위, DART: KOLMAR KOREA CO.,LTD
    "펩트론": ("Peptron",),  # 137위, DART: Peptron, Inc.
    "파마리서치": ("PharmaResearch", "Pharma Research",),  # 138위, DART: PharmaResearch Co., Ltd.
    "한온시스템": ("Hanon Systems",),  # 139위, DART: Hanon Systems
    "한국가스공사": ("KoreaGas", "Korea Gas",),  # 140위, DART: KoreaGasCorporation
    "신세계": ("SHINSEGAE",),  # 141위, DART: SHINSEGAE Inc.
    "티씨케이": ("TOKAI CARBON KOREA",),  # 142위, DART: TOKAI CARBON KOREA CO., LTD
    "테스": ("TESCO",),  # 145위, DART: TESCO.,LTD.
    "DL이앤씨": ("DL E&C",),  # 146위, DART: DL E&C CO.,LTD.
    "두산퓨얼셀": ("Doosan Fuel Cell",),  # 147위, DART: Doosan Fuel Cell Co., Ltd.
    "코스맥스": ("COSMAX",),  # 148위, DART: COSMAX, INC.
    "하나마이크론": ("HanaMicron", "Hana Micron",),  # 149위, DART: HanaMicronInc.
    "포스코DX": ("POSCO DX",),  # 152위, DART: POSCO DX COMPANY LTD.
    "팬오션": ("Pan Ocean",),  # 153위, DART: Pan Ocean Co., Ltd.
    "리가켐바이오": ("LigaChem Biosciences",),  # 154위, DART: LigaChem Biosciences Inc.
    "GS건설": ("GS Engineering & Construction",),  # 155위, DART: GS Engineering & Construction Corp
    "금호석유화학": ("KUMHO PETRO CHEMICAL",),  # 156위, DART: KUMHO PETRO CHEMICAL CO.,LTD
    "서울보증보험": ("Seoul Guarantee Insurance",),  # 158위, DART: Seoul Guarantee Insurance Company
    "현대엘리베이터": ("HYUNDAI ELEVATOR",),  # 159위, DART: HYUNDAI ELEVATOR CO.,LTD
    "솔브레인": ("Soulbrain",),  # 160위, DART: Soulbrain Co., Ltd.
    "에이비엘바이오": ("ABL Bio",),  # 162위, DART: ABL Bio Inc.
    "iM금융지주": ("iM Financial Group",),  # 163위, DART: iM Financial Group Co., Ltd.
    "SK바이오사이언스": ("SK Bioscience",),  # 164위, DART: SK Bioscience Co.,Ltd
    "DN오토모티브": ("DN AUTOMOTIVE",),  # 165위, DART: DN AUTOMOTIVE CORPORATION
    "대한광통신": ("TAIHAN Fiberoptics",),  # 166위, DART: TAIHAN Fiberoptics Co.,LTD
    "보로노이": ("Voronoi",),  # 168위, DART: Voronoi, Inc.
    "제주반도체": ("Jeju Semiconductor",),  # 169위, DART: Jeju Semiconductor Corp.
    "미래에셋생명": ("MIRAE ASSET Life Insurance",),  # 171위, DART: MIRAE ASSET Life Insurance CO., Ltd.
    "코리안리": ("Korean Reinsurance",),  # 173위, DART: Korean Reinsurance Company
    "한솔케미칼": ("Hansol Chemical",),  # 175위, DART: Hansol Chemical Co., Ltd
    "실리콘투": ("SILICON 2",),  # 176위, DART: SILICON 2 Co.,Ltd.
    "동서": ("DONG SUH COMPANIES",),  # 177위, DART: DONG SUH COMPANIES INC.
    "메지온": ("Mezzion Pharma",),  # 178위, DART: Mezzion Pharma Co., Ltd.
    "에코프로머티": ("ECOPRO MATERIALS",),  # 179위, DART: ECOPRO MATERIALS CO., LTD.
    "한올바이오파마": ("HANALL BIOPHARMA",),  # 180위, DART: HANALL BIOPHARMA CO.,LTD
    "한화비전": ("Hanwha Vision",),  # 182위, DART: Hanwha Vision CO.,LTD
    "현대지에프홀딩스": ("HYUNDAI G.F. HOLDINGS",),  # 183위, DART: HYUNDAI G.F. HOLDINGS CO., LTD.
    "동진쎄미켐": ("DONGJIN SEMICHEM",),  # 184위, DART: DONGJIN SEMICHEM CO., LTD.
    "휴젤": ("Hugel",),  # 186위, DART: Hugel, Inc.
    "한국앤컴퍼니": ("HANKOOK & COMPANY",),  # 187위, DART: HANKOOK & COMPANY CO., LTD.
    "올릭스": ("OliX Pharmaceuticals",),  # 188위, DART: OliX Pharmaceuticals, Inc.
    "현대무벡스": ("HYUNDAI MOVEX",),  # 189위, DART: HYUNDAI MOVEX Co., Ltd.
    "BGF리테일": ("BGF retail",),  # 190위, DART: BGF retail CO., LTD.
    "달바글로벌": ("d'Alba Global",),  # 191위, DART: d'Alba Global Co., Ltd.
    "신영증권": ("SHINYOUNG SECURITIES",),  # 192위, DART: SHINYOUNG SECURITIES CO.,LTD
    "우리기술": ("Woori Technology",),  # 194위, DART: Woori Technology, Incorporation
    "SK가스": ("SK GAS",),  # 196위, DART: SK GAS LTD
    "원익홀딩스": ("WONIK HOLDINGS",),  # 197위, DART: WONIK HOLDINGS CO.,LTD.
    "케이뱅크": ("Kbank",),  # 198위, DART: Kbank
    "케어젠": ("CAREGEN",),  # 199위, DART: CAREGEN CO.,LTD.
    "풍산": ("POONGSAN",),  # 200위, DART: POONGSAN CORPORATION
    "HL만도": ("HL Mando",),  # 201위, DART: HL Mando CO.,LTD
    "펄어비스": ("PearlAbyss", "Pearl Abyss",),  # 202위, DART: PearlAbyss Corp.
    "영원무역홀딩스": ("Youngone Holdings",),  # 203위, DART: Youngone Holdings Co., Ltd
    "씨에스윈드": ("CS Wind",),  # 204위, DART: CS Wind Corporation
    "롯데에너지머티리얼즈": ("LOTTE ENERGY MATERIALS",),  # 205위, DART: LOTTE ENERGY MATERIALS CORPORATION
    "현대백화점": ("HYUNDAI DEPARTMENT STORE",),  # 206위, DART: HYUNDAI DEPARTMENT STORE CO.,LTD
    "한전KPS": ("KEPCO Plant Service & Engineering",),  # 207위, DART: KEPCO Plant Service & Engineering Co., Ltd
    "서진시스템": ("SEOJIN SYSTEM",),  # 208위, DART: SEOJIN SYSTEM CO.,LTD
    "디앤디파마텍": ("D&D Pharmatech",),  # 209위, DART: D&D Pharmatech Inc.
    "제일기획": ("Cheil Worldwide",),  # 210위, DART: Cheil Worldwide Inc
    "아모레퍼시픽홀딩스": ("AMOREPACIFIC Holdings",),  # 211위, DART: AMOREPACIFIC Holdings Corp.
    "미스토홀딩스": ("Misto Holdings",),  # 212위, DART: Misto Holdings Corporation
    "클래시스": ("CLASSYS",),  # 213위, DART: CLASSYS Inc.
    "고영": ("KohYoungTechnology", "Koh Young Technology",),  # 214위, DART: KohYoungTechnologyInc.
    "성호전자": ("SUNGHO ELECTRONICS",),  # 215위, DART: SUNGHO ELECTRONICS CORP.
    "LS에코에너지": ("LS Eco Energy",),  # 217위, DART: LS Eco Energy Ltd.
    "이수스페셜티케미컬": ("ISU SPECIALTY CHEMICAL",),  # 218위, DART: ISU SPECIALTY CHEMICAL CO., LTD.
    "GS리테일": ("GS Retail",),  # 219위, DART: GS Retail Co.,Ltd
    "에스티팜": ("ST PHARM",),  # 220위, DART: ST PHARM CO., LTD.
    "에스엠": ("SM ENTERTAINMENT",),  # 221위, DART: SM ENTERTAINMENT CO., Ltd.
    "금호타이어": ("KUMHO TIRE",),  # 222위, DART: KUMHO TIRE CO., INC.
    "시프트업": ("SHIFT UP",),  # 224위, DART: SHIFT UP Corporation.
    "파크시스템스": ("Park Systems",),  # 225위, DART: Park Systems Corp.
    "테크윙": ("Techwing",),  # 226위, DART: Techwing, Inc.
    "두산테스나": ("DOOSAN TESNA",),  # 227위, DART: DOOSAN TESNA Inc.
    "롯데렌탈": ("LOTTE rental",),  # 229위, DART: LOTTE rental co., ltd.
    "LS마린솔루션": ("LS Marine Solution",),  # 230위, DART: LS Marine Solution Co., Ltd.
    "SFA반도체": ("SFA Semicon",),  # 231위, DART: SFA Semicon Co., Ltd
    "셀트리온제약": ("Celltrion Pharm",),  # 232위, DART: Celltrion Pharm Inc.
    "대한조선": ("DAEHAN SHIPBUILDING",),  # 233위, DART: DAEHAN SHIPBUILDING Co., Ltd.
    "SK이터닉스": ("SK eternix",),  # 234위, DART: SK eternix Co., Ltd.
    "아시아나항공": ("Asiana Airlines",),  # 235위, DART: Asiana Airlines Inc.
    "케이씨텍": ("KCTECH",),  # 236위, DART: KCTECH CO., LTD
    "다우기술": ("DAOU TECHNOLOGY",),  # 237위, DART: DAOU TECHNOLOGY INC
    "HD현대마린엔진": ("HD-Hyundai Marine Engine",),  # 238위, DART: HD-Hyundai Marine Engine Co., Ltd.
    "삼현": ("SAMHYUN",),  # 239위, DART: SAMHYUN CO.,LTD
    "코미코": ("KoMiCo",),  # 241위, DART: KoMiCo Ltd.
    "코오롱인더": ("KOLON INDUSTRIES",),  # 242위, DART: KOLON INDUSTRIES, INC.
    "SK리츠": ("SK REIT",),  # 243위, DART: SK REIT Co., Ltd.
    "CJ대한통운": ("CJ Logistics",),  # 244위, DART: CJ Logistics Corporation
    "씨젠": ("SEEGENE",),  # 245위, DART: SEEGENE, INC.
    "세아베스틸지주": ("SeAH Besteel Holdings",),  # 246위, DART: SeAH Besteel Holdings Corporation
    "네이처셀": ("NATURECELL",),  # 247위, DART: NATURECELL CO.,LTD.
    "호텔신라": ("HOTEL SHILLA",),  # 248위, DART: HOTEL SHILLA CO.,LTD
    "IPARK현대산업개발": ("IPARK HYUNDAI DEVELOPMENT",),  # 249위, DART: IPARK HYUNDAI DEVELOPMENT COMPANY
    "코리아써키트": ("KOREA CIRCUIT",),  # 250위, DART: KOREA CIRCUIT CO.,LTD
    "기가비스": ("GigaVis", "Giga Vis",),  # 251위, DART: GigaVis Co., Ltd.
    "LX인터내셔널": ("LX INTERNATIONAL",),  # 252위, DART: LX INTERNATIONAL CORP.
    "동원산업": ("DONGWON INDUSTRIES",),  # 254위, DART: DONGWON INDUSTRIES CO.,LTD
    "삼화콘덴서": ("SAMWHA CAPACITOR",),  # 255위, DART: SAMWHA CAPACITOR CO.,LTD
    "HDC": ("HDC HOLDINGS",),  # 256위, DART: HDC HOLDINGS CO.,Ltd
    "후성": ("FOOSUNG",),  # 257위, DART: FOOSUNG CO.,LTD
    "녹십자": ("GC Biopharma",),  # 258위, DART: GC Biopharma Corp.
    "오리온홀딩스": ("ORION Holdings",),  # 259위, DART: ORION Holdings Corp.
    "현대위아": ("HYUNDAI WIA",),  # 260위, DART: HYUNDAI WIA
    "SK아이이테크놀로지": ("SK ie technology",),  # 262위, DART: SK ie technology Co., Ltd.
    "에임드바이오": ("Aimed Bio",),  # 263위, DART: Aimed Bio Inc.
    "코스모신소재": ("COSMO ADVANCED MATERIALS & TECHNOLOGY",),  # 264위, DART: COSMO ADVANCED MATERIALS & TECHNOLOGY CO., LTD
    "제이앤티씨": ("JNTC",),  # 265위, DART: JNTC CO.,LTD.
    "오스코텍": ("OSCOTECInc",),  # 266위, DART: OSCOTECInc.
    "코스메카코리아": ("COSMECCA KOREA",),  # 267위, DART: COSMECCA KOREA CO., LTD.
    "대주전자재료": ("DaejooElectronicMaterialsCo", "Daejoo Electronic Materials Co",),  # 268위, DART: DaejooElectronicMaterialsCo.,Ltd.
    "효성티앤씨": ("Hyosung TNC",),  # 269위, DART: Hyosung TNC Corporation
    "HD현대에너지솔루션": ("HD HYUNDAI ENERGY SOLUTIONS",),  # 270위, DART: HD HYUNDAI ENERGY SOLUTIONS CO.,LTD.
    "코오롱티슈진": ("Kolon TissueGene",),  # 271위, DART: Kolon TissueGene, Inc.
    "JYP Ent.": ("JYP Entertainment",),  # 272위, DART: JYP Entertainment Corporation
    "HJ중공업": ("HJ SHIPBUILDING & CONSTRUCTION",),  # 273위, DART: HJ SHIPBUILDING & CONSTRUCTION CO.,LTD
    "엘앤씨바이오": ("L&C BIO",),  # 274위, DART: L&C BIO Co., Ltd
    "비츠로셀": ("VITZROCELL",),  # 275위, DART: VITZROCELL Co.,Ltd.
    "KB발해인프라": ("KB Balhae Infrastructure Fund",),  # 276위, DART: KB Balhae Infrastructure Fund
    "오뚜기": ("OTOKI",),  # 277위, DART: OTOKI CORPORATION
    "대신증권": ("DAISHIN SECURITIES",),  # 278위, DART: DAISHIN SECURITIES CO.,LTD
    "하림지주": ("Harim Holdings",),  # 279위, DART: Harim Holdings Co., Ltd.
    "더블유게임즈": ("DoubleUGames", "Double UGames",),  # 280위, DART: DoubleUGames Co., Ltd.
    "대웅제약": ("DAEWOONG PHARMA",),  # 281위, DART: DAEWOONG PHARMA
    "SK네트웍스": ("SKNetworks", "SK Networks",),  # 282위, DART: SKNetworksCompanyLimited
    "인제니아테라퓨틱스(Reg.S)": ("INGENIA Therapeutics",),  # 283위, DART: INGENIA Therapeutics Inc.
    "롯데리츠": ("LOTTE REIT",),  # 285위, DART: LOTTE REIT Co., Ltd.
    "한일시멘트": ("HANILCEMENT", "Hanil Cement",),  # 286위, DART: HANILCEMENTCO.,LTD.
    "제이에스링크": ("JS Link",),  # 287위, DART: JS Link, Inc.
    "하나머티리얼즈": ("Hana Materials",),  # 289위, DART: Hana Materials Inc.
    "롯데웰푸드": ("LOTTE WELLFOOD",),  # 290위, DART: LOTTE WELLFOOD CO.,LTD
    "교보증권": ("KYOBO SECURITIES",),  # 291위, DART: KYOBO SECURITIES CO.,LTD.
    "롯데정밀화학": ("LOTTE Fine Chemical",),  # 292위, DART: LOTTE Fine Chemical Co., Ltd.
    "HK이노엔": ("HK inno.N",),  # 293위, DART: HK inno.N Corporation
    "샘씨엔에스": ("SEMCNS",),  # 294위, DART: SEMCNS Co., Ltd.
    "DL": ("DL Holdings",),  # 295위, DART: DL Holdings CO.,LTD
    "대원전선": ("DAWON CABLE",),  # 296위, DART: DAWON CABLE CO.,LTD
    "하이트진로": ("HITE JINRO",),  # 298위, DART: HITE JINRO
    "SK디스커버리": ("SK Discovery",),  # 299위, DART: SK Discovery Co.,Ltd.
    "한국카본": ("HANKUK CARBON",),  # 300위, DART: HANKUK CARBON CO.,LTD
}
