"""일일 투자 브리핑(app/daily_briefing)의 언어별 문장 템플릿(ko-KR·en-US·ja-JP·zh-CN, 2026-11-06).

숫자·날짜·종목코드·회사 공식 명칭·뉴스/공시 제목은 템플릿 밖에서 그대로 끼워 넣는다(번역하지 않는다). 숫자 표기는 언어와 관계없이
같은 자리수다(등락률 +1.23%, 금액은 천 단위 쉼표, 통화 기호만 언어별).
"""

from __future__ import annotations

LOCALES = ("ko-KR", "en-US", "ja-JP", "zh-CN")

T: dict[str, dict[str, str]] = {
    # ---------- 영역 제목 ----------
    "title_market": {"ko-KR": "오늘의 시장 요약", "en-US": "Today's market summary", "ja-JP": "今日の市場概況", "zh-CN": "今日市场概览"},
    "title_holdings": {"ko-KR": "내 보유 종목 주요 변화", "en-US": "Key changes in my holdings", "ja-JP": "保有銘柄の主な変化", "zh-CN": "持仓主要变化"},
    "title_watchlist": {"ko-KR": "관심 종목 업데이트", "en-US": "Watchlist updates", "ja-JP": "ウォッチリストの更新", "zh-CN": "关注股票动态"},
    "title_risk": {"ko-KR": "포트폴리오 위험 점검", "en-US": "Portfolio risk check", "ja-JP": "ポートフォリオのリスク点検", "zh-CN": "投资组合风险检查"},
    "title_checklist": {"ko-KR": "오늘 확인할 사항", "en-US": "Things to check today", "ja-JP": "今日確認すること", "zh-CN": "今日需确认事项"},
    "title_briefing": {"ko-KR": "{date} 일일 투자 브리핑", "en-US": "Daily investment briefing for {date}", "ja-JP": "{date} デイリー投資ブリーフィング",
                       "zh-CN": "{date} 每日投资简报"},
    # ---------- 데이터 신선도 ----------
    "fresh_latest": {"ko-KR": "시세는 최신 거래일({base}) 종가 기준입니다.", "en-US": "Prices are based on the latest trading day's close ({base}).",
                     "ja-JP": "株価は最新取引日（{base}）の終値基準です。", "zh-CN": "股价以最新交易日（{base}）收盘价为准。"},
    "fresh_delayed": {
        "ko-KR": "시세가 {lag}영업일 지연됐습니다. 마지막 종가는 {base}, 기대 거래일은 {exp}입니다{why}. 아래 숫자는 {base} 종가 기준이며 오늘의 시장 상황이 아닙니다.",
        "en-US": "Prices are {lag} business day(s) behind. The latest close is {base} and the expected trading day is {exp}{why}. "
                 "The figures below are based on the {base} close, not today's market.",
        "ja-JP": "株価が{lag}営業日遅れています。最新の終値は{base}、想定取引日は{exp}です{why}。以下の数値は{base}終値基準で、今日の市場状況ではありません。",
        "zh-CN": "股价滞后{lag}个工作日。最新收盘价日期为{base}，预期交易日为{exp}{why}。以下数据以{base}收盘价为准，并非今日市场情况。"},
    "fresh_stale": {
        "ko-KR": "시세가 {lag}영업일 지연돼 5영업일을 넘었습니다. 마지막 종가는 {base}, 기대 거래일은 {exp}입니다{why}. 오래된 데이터라 최근 변화 항목은 오늘의 상황을 나타내지 않습니다.",
        "en-US": "Prices are {lag} business days behind, more than 5 business days. The latest close is {base} and the expected trading day is {exp}{why}. "
                 "Because the data is old, the recent-change items do not reflect today's situation.",
        "ja-JP": "株価が{lag}営業日遅れており、5営業日を超えています。最新の終値は{base}、想定取引日は{exp}です{why}。古いデータのため、最近の変化の項目は今日の状況を表していません。",
        "zh-CN": "股价滞后{lag}个工作日，已超过5个工作日。最新收盘价日期为{base}，预期交易日为{exp}{why}。由于数据较旧，近期变化项目并不反映今日情况。"},
    "fresh_none": {"ko-KR": "수집된 시세 데이터가 없습니다. 시세가 필요한 항목은 데이터 부족으로 표시합니다.",
                   "en-US": "No price data has been collected. Items that need prices are marked as insufficient data.",
                   "ja-JP": "収集された株価データがありません。株価が必要な項目はデータ不足と表示します。",
                   "zh-CN": "尚未收集到股价数据。需要股价的项目标记为数据不足。"},
    "fresh_why": {"ko-KR": "({status})", "en-US": " ({status})", "ja-JP": "（{status}）", "zh-CN": "（{status}）"},
    "fresh_source_lag": {"ko-KR": "{source} 데이터는 {date} 기준입니다({lag}영업일 지연).", "en-US": "{source} data is as of {date} ({lag} business day(s) behind).",
                         "ja-JP": "{source}のデータは{date}時点です（{lag}営業日遅れ）。", "zh-CN": "{source}数据截至{date}（滞后{lag}个工作日）。"},
    "fresh_source_none": {"ko-KR": "{source} 데이터가 없습니다.", "en-US": "No {source} data is available.", "ja-JP": "{source}のデータがありません。",
                          "zh-CN": "没有{source}数据。"},
    "fresh_refresh_failed": {"ko-KR": "마지막 데이터 수집({at})에서 실패한 단계가 있습니다: {steps}.",
                             "en-US": "Some steps failed in the last data collection ({at}): {steps}.",
                             "ja-JP": "最後のデータ収集（{at}）で失敗した段階があります: {steps}。", "zh-CN": "最近一次数据采集（{at}）中有失败的步骤：{steps}。"},
    "fresh_intraday": {"ko-KR": "장 마감 전이라 오늘 시세는 쓰지 않고 직전 거래일 종가를 씁니다.",
                       "en-US": "The market has not closed yet, so today's prices are not used; the previous trading day's close is used instead.",
                       "ja-JP": "取引終了前のため、今日の株価は使わず直前取引日の終値を使います。", "zh-CN": "尚未收盘，因此不使用今日行情，而使用前一交易日收盘价。"},
    "src_index_kospi": {"ko-KR": "코스피 지수", "en-US": "KOSPI index", "ja-JP": "KOSPI指数", "zh-CN": "KOSPI指数"},
    "src_index_kosdaq": {"ko-KR": "코스닥 지수", "en-US": "KOSDAQ index", "ja-JP": "KOSDAQ指数", "zh-CN": "KOSDAQ指数"},
    "src_fx": {"ko-KR": "원/달러 환율", "en-US": "USD/KRW exchange rate", "ja-JP": "ウォン/ドル為替", "zh-CN": "韩元/美元汇率"},
    # ---------- 시장 ----------
    "mkt_line": {"ko-KR": "{name} {date} 종가 {close} · 전일 대비 {d1} · 5거래일 {d5}",
                 "en-US": "{name} close on {date}: {close} · {d1} vs. previous day · {d5} over 5 trading days",
                 "ja-JP": "{name} {date} 終値 {close} · 前日比 {d1} · 5取引日 {d5}", "zh-CN": "{name} {date} 收盘 {close} · 较前日 {d1} · 5个交易日 {d5}"},
    "mkt_none": {"ko-KR": "{name}: 데이터 없음", "en-US": "{name}: no data", "ja-JP": "{name}: データなし", "zh-CN": "{name}：无数据"},
    "na": {"ko-KR": "계산 불가", "en-US": "not available", "ja-JP": "計算不可", "zh-CN": "无法计算"},
    "mkt_news_label": {"ko-KR": "시장 관련 기사(제목에 시장 키워드가 있는 기사, 제목만 보이며 해석하지 않습니다)",
                       "en-US": "Market-related articles (headlines containing market keywords; headlines only, not interpreted)",
                       "ja-JP": "市場関連記事（見出しに市場キーワードがある記事。見出しのみで解釈はしません）",
                       "zh-CN": "市场相关文章（标题含市场关键词的文章，仅显示标题，不作解读）"},
    "mkt_news_none": {"ko-KR": "최근 {n}일 안에 시장 키워드가 제목에 있는 기사가 없습니다.",
                      "en-US": "No articles with market keywords in the headline in the last {n} days.",
                      "ja-JP": "直近{n}日以内に見出しに市場キーワードがある記事はありません。", "zh-CN": "最近{n}天内没有标题含市场关键词的文章。"},
    "market_kospi": {"ko-KR": "코스피", "en-US": "KOSPI", "ja-JP": "KOSPI", "zh-CN": "KOSPI"},
    "market_kosdaq": {"ko-KR": "코스닥", "en-US": "KOSDAQ", "ja-JP": "KOSDAQ", "zh-CN": "KOSDAQ"},
    "market_usd_krw": {"ko-KR": "원/달러", "en-US": "USD/KRW", "ja-JP": "ウォン/ドル", "zh-CN": "韩元/美元"},
    # ---------- 보유·관심 종목 ----------
    "item_price": {"ko-KR": "{date} 종가 {close} · 전일 대비 {d1} · 5거래일 {d5} · 20거래일 {d20}",
                   "en-US": "Close on {date}: {close} · {d1} vs. previous day · {d5} over 5 trading days · {d20} over 20 trading days",
                   "ja-JP": "{date} 終値 {close} · 前日比 {d1} · 5取引日 {d5} · 20取引日 {d20}",
                   "zh-CN": "{date} 收盘 {close} · 较前日 {d1} · 5个交易日 {d5} · 20个交易日 {d20}"},
    "item_no_price": {"ko-KR": "시세 데이터가 없어 등락률을 계산하지 못했습니다.", "en-US": "No price data, so returns could not be calculated.",
                      "ja-JP": "株価データがないため騰落率を計算できませんでした。", "zh-CN": "没有股价数据，无法计算涨跌幅。"},
    "item_position": {"ko-KR": "보유 {qty}주 · 평가액 {eval} · 비중 {w} · 평균 매입가 대비 {pl}",
                      "en-US": "{qty} shares held · value {eval} · weight {w} · {pl} vs. average purchase price",
                      "ja-JP": "保有{qty}株 · 評価額 {eval} · 比率 {w} · 平均取得単価比 {pl}",
                      "zh-CN": "持有{qty}股 · 市值 {eval} · 权重 {w} · 相对平均买入价 {pl}"},
    "item_older_price": {"ko-KR": "이 종목의 시세는 {date} 기준으로 브리핑 기준일({base})보다 오래됐습니다.",
                         "en-US": "This item's price is as of {date}, older than the briefing date ({base}).",
                         "ja-JP": "この銘柄の株価は{date}時点で、ブリーフィング基準日（{base}）より古いです。",
                         "zh-CN": "该股票的行情截至{date}，早于简报基准日（{base}）。"},
    "item_no_news": {"ko-KR": "최근 {n}일 직접 관련 뉴스가 없습니다.", "en-US": "No directly related news in the last {n} days.",
                     "ja-JP": "直近{n}日に直接関連するニュースはありません。", "zh-CN": "最近{n}天没有直接相关的新闻。"},
    "item_no_disclosure": {"ko-KR": "최근 {n}일 공시가 없습니다.", "en-US": "No filings in the last {n} days.", "ja-JP": "直近{n}日に開示はありません。",
                           "zh-CN": "最近{n}天没有公告。"},
    "item_etf_no_disclosure": {"ko-KR": "ETF는 공시를 수집하지 않습니다.", "en-US": "Filings are not collected for ETFs.", "ja-JP": "ETFの開示は収集していません。",
                               "zh-CN": "不收集ETF的公告。"},
    "news_direct_label": {"ko-KR": "직접 관련 뉴스", "en-US": "Directly related news", "ja-JP": "直接関連ニュース", "zh-CN": "直接相关新闻"},
    "news_sector_label": {"ko-KR": "업종·산업 뉴스(간접)", "en-US": "Sector and industry news (indirect)", "ja-JP": "業種・産業ニュース（間接）",
                          "zh-CN": "行业·产业新闻（间接）"},
    "news_index_label": {"ko-KR": "기초지수 관련 뉴스(간접)", "en-US": "Underlying index news (indirect)", "ja-JP": "基礎指数関連ニュース（間接）",
                         "zh-CN": "基础指数相关新闻（间接）"},
    "news_grouped": {"ko-KR": "비슷한 제목 {n}건을 하나로 묶었습니다.", "en-US": "{n} articles with similar headlines were grouped into one.",
                     "ja-JP": "似た見出しの記事{n}件をまとめました。", "zh-CN": "已将{n}篇标题相似的文章合并为一条。"},
    "holdings_none": {"ko-KR": "보유 중인 종목이 없습니다. 모의투자에서 주문하면 보유 종목 변화가 표시됩니다.",
                      "en-US": "You have no holdings. Changes in your holdings will appear after you place a simulated trade.",
                      "ja-JP": "保有中の銘柄がありません。模擬投資で注文すると保有銘柄の変化が表示されます。",
                      "zh-CN": "当前没有持仓。在模拟投资中下单后将显示持仓变化。"},
    "watch_none": {"ko-KR": "관심 종목이 없습니다. 관심 종목을 추가하면 업데이트를 볼 수 있습니다.",
                   "en-US": "Your watchlist is empty. Add stocks to see updates.", "ja-JP": "ウォッチリストに銘柄がありません。銘柄を追加すると更新を確認できます。",
                   "zh-CN": "关注列表为空。添加股票后即可查看动态。"},
    "watch_all_in_holdings": {"ko-KR": "관심 종목이 모두 보유 종목이라 보유 종목 영역에만 표시했습니다.",
                              "en-US": "All watchlist items are also holdings, so they are shown only in the holdings section.",
                              "ja-JP": "ウォッチリストの銘柄はすべて保有銘柄のため、保有銘柄の欄にのみ表示しました。",
                              "zh-CN": "关注股票均为持仓股票，因此仅在持仓部分显示。"},
    "watch_no_update": {"ko-KR": "특별한 업데이트가 없습니다.", "en-US": "No notable updates.", "ja-JP": "特筆すべき更新はありません。", "zh-CN": "没有特别的动态。"},
    "watch_dedup": {"ko-KR": "보유 종목과 겹치는 관심 종목 {n}개는 보유 종목 영역에만 표시했습니다.",
                    "en-US": "{n} watchlist item(s) that are also holdings are shown only in the holdings section.",
                    "ja-JP": "保有銘柄と重なるウォッチリストの銘柄{n}件は保有銘柄の欄にのみ表示しました。",
                    "zh-CN": "与持仓重复的{n}只关注股票仅在持仓部分显示。"},
    "since_research": {"ko-KR": "마지막 리서치({at}) 이후 저장된 뉴스 {news}건, 공시 {disc}건이 있습니다.",
                       "en-US": "Since your last research ({at}), {news} news article(s) and {disc} filing(s) have been stored.",
                       "ja-JP": "最後のリサーチ（{at}）以降に保存されたニュースは{news}件、開示は{disc}件です。",
                       "zh-CN": "自上次研究（{at}）以来，已保存新闻{news}条、公告{disc}条。"},
    # ---------- 위험 ----------
    "risk_metrics": {"ko-KR": "상위 1종목 비중 {top1} · 상위 3종목 비중 {top3} · 현금 비중 {cash} · 보유 종목 {n}개",
                     "en-US": "Top holding weight {top1} · top 3 holdings weight {top3} · cash weight {cash} · {n} holding(s)",
                     "ja-JP": "上位1銘柄の比率 {top1} · 上位3銘柄の比率 {top3} · 現金比率 {cash} · 保有銘柄{n}件",
                     "zh-CN": "前1大持仓权重 {top1} · 前3大持仓权重 {top3} · 现金权重 {cash} · 持仓{n}只"},
    "risk_no_flags": {"ko-KR": "규칙 기준으로 확인된 위험 항목은 없습니다.", "en-US": "No risk items were found under the rules.",
                      "ja-JP": "ルール基準で確認されたリスク項目はありません。", "zh-CN": "按规则未发现风险项目。"},
    "risk_sector_unavailable": {"ko-KR": "업종 분류 데이터가 없어 업종 집중도는 계산하지 않았습니다.",
                                "en-US": "Sector concentration was not calculated because sector classification data is unavailable.",
                                "ja-JP": "業種分類データがないため、業種の集中度は計算していません。", "zh-CN": "由于没有行业分类数据，未计算行业集中度。"},
    "risk_etf_computed": {"ko-KR": "ETF 구성종목 비중을 반영한 기업별 실질 노출을 계산했습니다(구성종목 기준일 {dates}).",
                          "en-US": "Look-through exposure by company was calculated using ETF constituent weights (constituents as of {dates}).",
                          "ja-JP": "ETF構成銘柄の比率を反映した企業別の実質エクスポージャーを計算しました（構成銘柄基準日 {dates}）。",
                          "zh-CN": "已根据ETF成分股权重计算按公司的实际敞口（成分股基准日 {dates}）。"},
    "risk_etf_not_computed": {"ko-KR": "{names}는 실질 노출을 계산하지 않았습니다(구성종목 데이터 없음 또는 레버리지·인버스).",
                              "en-US": "Look-through exposure was not calculated for {names} (no constituent data, or leveraged/inverse).",
                              "ja-JP": "{names}は実質エクスポージャーを計算していません（構成銘柄データなし、またはレバレッジ・インバース）。",
                              "zh-CN": "未计算{names}的实际敞口（无成分股数据或为杠杆/反向）。"},
    "risk_etf_none": {"ko-KR": "보유 ETF가 없어 ETF 실질 노출 분해는 해당되지 않습니다.", "en-US": "No ETFs are held, so ETF look-through does not apply.",
                      "ja-JP": "保有ETFがないため、ETFの実質エクスポージャー分解は該当しません。", "zh-CN": "未持有ETF，因此不涉及ETF实际敞口分解。"},
    "risk_no_holdings": {"ko-KR": "보유 종목이 없어 포트폴리오 위험을 점검하지 않았습니다.", "en-US": "There are no holdings, so the portfolio risk check was not run.",
                         "ja-JP": "保有銘柄がないため、ポートフォリオのリスクを点検していません。", "zh-CN": "没有持仓，因此未进行投资组合风险检查。"},
    "risk_unpriced": {"ko-KR": "{names}의 시세가 없어 비중·집중도 계산에서 뺐습니다.", "en-US": "{names} had no prices and were excluded from weight and concentration calculations.",
                      "ja-JP": "{names}は株価がないため、比率・集中度の計算から除外しました。", "zh-CN": "{names}没有行情，已从权重和集中度计算中排除。"},
    "caution_stale": {"ko-KR": "시세가 {lag}영업일 지연돼 위험 점검도 {base} 종가 기준입니다.", "en-US": "Prices are {lag} business day(s) behind, so this risk check is also based on the {base} close.",
                      "ja-JP": "株価が{lag}営業日遅れているため、リスク点検も{base}終値基準です。", "zh-CN": "股价滞后{lag}个工作日，因此风险检查也以{base}收盘价为准。"},
    # ---------- 확인할 사항 ----------
    "check_delay": {"ko-KR": "시세가 {lag}영업일 지연돼 있어 최신 거래일({exp}) 내용이 반영되지 않았습니다.",
                    "en-US": "Prices are {lag} business day(s) behind, so the latest trading day ({exp}) is not reflected.",
                    "ja-JP": "株価が{lag}営業日遅れており、最新取引日（{exp}）の内容は反映されていません。",
                    "zh-CN": "股价滞后{lag}个工作日，最新交易日（{exp}）的情况尚未反映。"},
    "check_no_data": {"ko-KR": "수집된 시세가 없어 가격 관련 내용을 계산하지 못했습니다.", "en-US": "No prices have been collected, so price-related items could not be calculated.",
                      "ja-JP": "株価が収集されていないため、価格関連の内容を計算できませんでした。", "zh-CN": "尚未收集到行情，无法计算价格相关内容。"},
    "check_disclosure": {"ko-KR": "{name}({ticker})의 최근 공시: {title} ({date})", "en-US": "Recent filing by {name}({ticker}): {title} ({date})",
                         "ja-JP": "{name}({ticker})の最近の開示: {title}（{date}）", "zh-CN": "{name}({ticker})的最新公告：{title}（{date}）"},
    "check_earnings": {"ko-KR": "{name}({ticker})의 실적 관련 공시: {title} ({date})", "en-US": "Earnings-related filing by {name}({ticker}): {title} ({date})",
                       "ja-JP": "{name}({ticker})の業績関連の開示: {title}（{date}）", "zh-CN": "{name}({ticker})的业绩相关公告：{title}（{date}）"},
    "check_none": {"ko-KR": "오늘 따로 확인할 항목이 없습니다.", "en-US": "There is nothing in particular to check today.", "ja-JP": "今日は特に確認する項目はありません。",
                   "zh-CN": "今日没有需要特别确认的项目。"},
    # ---------- 요약 ----------
    "summary_code": {"ko-KR": "{base} 종가 기준 브리핑입니다. 보유 {h}종목, 관심 {w}종목을 확인했고 규칙 기준 주목할 변화는 {k}건, 오늘 확인할 사항은 {c}건입니다.",
                     "en-US": "This briefing is based on the {base} close. It covers {h} holding(s) and {w} watchlist item(s), with {k} notable change(s) under the rules and {c} item(s) to check today.",
                     "ja-JP": "{base}終値基準のブリーフィングです。保有{h}銘柄、ウォッチリスト{w}銘柄を確認し、ルール基準で注目すべき変化は{k}件、今日確認することは{c}件です。",
                     "zh-CN": "本简报以{base}收盘价为准。共检查持仓{h}只、关注股票{w}只，按规则值得关注的变化{k}项，今日需确认事项{c}项。"},
    "summary_code_none": {"ko-KR": "시세 데이터가 없어 기준일을 정하지 못했습니다. 보유 {h}종목, 관심 {w}종목을 확인했습니다.",
                          "en-US": "No price data, so no base date could be set. It covers {h} holding(s) and {w} watchlist item(s).",
                          "ja-JP": "株価データがないため基準日を決められませんでした。保有{h}銘柄、ウォッチリスト{w}銘柄を確認しました。",
                          "zh-CN": "没有股价数据，无法确定基准日。共检查持仓{h}只、关注股票{w}只。"},
    "summary_llm_note": {"ko-KR": "AI가 위 계산 결과만으로 쓴 요약입니다(숫자 검증 통과).", "en-US": "Summary written by AI from the calculated results above only (passed number checks).",
                         "ja-JP": "AIが上記の計算結果のみから作成した要約です（数値検証済み）。", "zh-CN": "由AI仅根据上述计算结果撰写的摘要（已通过数字校验）。"},
    "summary_code_note": {"ko-KR": "코드가 계산 결과로 만든 요약입니다.", "en-US": "Summary generated by code from the calculated results.",
                          "ja-JP": "コードが計算結果から作成した要約です。", "zh-CN": "由代码根据计算结果生成的摘要。"},
    "not_advice": {"ko-KR": "확인된 데이터를 정리한 것이며 매수·매도 권유나 가격 예측이 아닙니다.",
                   "en-US": "This summarizes verified data; it is not a recommendation to buy or sell or a price forecast.",
                   "ja-JP": "確認されたデータを整理したもので、売買の推奨や価格予測ではありません。", "zh-CN": "以上为已确认数据的整理，并非买卖建议或价格预测。"},
}

# 업종·기초지수 이름(app.related_news)
TOPIC = {
    "반도체": {"en-US": "semiconductors", "ja-JP": "半導体", "zh-CN": "半导体"},
    "2차전지": {"en-US": "secondary batteries", "ja-JP": "二次電池", "zh-CN": "二次电池"},
    "전력설비": {"en-US": "power equipment", "ja-JP": "電力設備", "zh-CN": "电力设备"},
    "코스피200": {"en-US": "KOSPI 200", "ja-JP": "KOSPI 200", "zh-CN": "KOSPI 200"},
    "코스닥150": {"en-US": "KOSDAQ 150", "ja-JP": "KOSDAQ 150", "zh-CN": "KOSDAQ 150"},
    "나스닥100": {"en-US": "Nasdaq-100", "ja-JP": "Nasdaq-100", "zh-CN": "Nasdaq-100"},
    "S&P500": {"en-US": "S&P 500", "ja-JP": "S&P 500", "zh-CN": "S&P 500"},
    "필라델피아반도체지수": {"en-US": "PHLX Semiconductor Index", "ja-JP": "フィラデルフィア半導体指数", "zh-CN": "费城半导体指数"},
    "다우존스": {"en-US": "Dow Jones", "ja-JP": "ダウ・ジョーンズ", "zh-CN": "道琼斯"},
}
_CURRENCY = {"ko-KR": ("", "원"), "en-US": ("KRW ", ""), "ja-JP": ("", "ウォン"), "zh-CN": ("", "韩元")}
_SEP = {"ko-KR": ", ", "en-US": ", ", "ja-JP": "、", "zh-CN": "、"}


def t(key: str, locale: str, **kw) -> str:
    return T[key][locale].format(**kw)


def topic(name: str | None, locale: str) -> str | None:
    if name is None or locale == "ko-KR":
        return name
    return TOPIC.get(name, {}).get(locale, name)


def won(v: float | None, locale: str) -> str:
    if v is None:
        return T["na"][locale]
    num = f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"
    pre, post = _CURRENCY[locale]
    return f"{pre}{num}{post}"


def pct(v: float | None, locale: str, signed: bool = True) -> str:
    if v is None:
        return T["na"][locale]
    return f"{v:+.2f}%" if signed else f"{v:.2f}%"


def num(v: float | None, locale: str) -> str:
    if v is None:
        return T["na"][locale]
    return f"{v:,.2f}"


def join(names: list[str], locale: str) -> str:
    return _SEP[locale].join(names)
