"""투자 용어 사전의 언어별 정의(2026-11-05, en-US·ja-JP·zh-CN). ko-KR은 app.glossary를 그대로 쓴다.

정의는 app.glossary.GLOSSARY의 한국어 정의와 같은 사실만 옮겼다(새 사실·수치를 더하지 않았다). 예시 숫자와 제도 수치
(±30%, 2영업일, 1980년 1월 4일 = 100)도 한국어판과 같다. 질문 판정(정의·비교·해석 질문인지)은 app.glossary의 규칙을 그대로 쓰고,
답 문장만 바꾼다. 모든 표제어가 세 언어에 다 있어야 한다(tests/test_glossary_i18n.py).
"""

from app import glossary as g

L = ("en-US", "ja-JP", "zh-CN")

# 한국어 표제어 -> {locale: (표제어, 정의)}
DEFINITIONS: dict[str, dict[str, tuple[str, str]]] = {
    "PER(주가수익비율)": {
        "en-US": ("PER (price-to-earnings ratio)",
                  "The share price divided by earnings per share (EPS). PER = share price ÷ EPS (= market capitalization ÷ net income). "
                  "It shows how many times the company's earnings the stock trades at; if net income is negative, the value is not meaningful. "
                  "Typical levels differ by industry, so it is usually compared among companies in the same industry."),
        "ja-JP": ("PER（株価収益率）",
                  "株価を1株当たり純利益（EPS）で割った値です。PER = 株価 ÷ EPS（= 時価総額 ÷ 当期純利益）。"
                  "会社が稼ぐ利益に対して株価が何倍で取引されているかを示し、純利益が赤字の場合は意味のある値になりません。"
                  "業種ごとに一般的な水準が異なるため、同じ業種同士で比較するのが普通です。"),
        "zh-CN": ("PER（市盈率）",
                  "股价除以每股收益（EPS）得到的值。PER = 股价 ÷ EPS（= 总市值 ÷ 当期净利润）。"
                  "表示股价相当于公司盈利的多少倍；若净利润为亏损，则得不到有意义的数值。"
                  "不同行业的常见水平不同，因此通常在同一行业的公司之间比较。"),
    },
    "PBR(주가순자산비율)": {
        "en-US": ("PBR (price-to-book ratio)",
                  "The share price divided by book value per share (BPS). PBR = share price ÷ BPS (= market capitalization ÷ net assets). "
                  "A PBR below 1 means the stock trades below the book net assets (total equity) divided by the number of shares."),
        "ja-JP": ("PBR（株価純資産倍率）",
                  "株価を1株当たり純資産（BPS）で割った値です。PBR = 株価 ÷ BPS（= 時価総額 ÷ 純資産）。"
                  "PBRが1より低い場合、株価が帳簿上の純資産（自己資本合計）を株式数で割った値より低く取引されていることを意味します。"),
        "zh-CN": ("PBR（市净率）",
                  "股价除以每股净资产（BPS）得到的值。PBR = 股价 ÷ BPS（= 总市值 ÷ 净资产）。"
                  "PBR低于1表示股价低于账面净资产（股东权益合计）除以股数得到的值。"),
    },
    "ROE(자기자본이익률)": {
        "en-US": ("ROE (return on equity)",
                  "Net income divided by shareholders' equity. ROE = net income ÷ shareholders' equity × 100(%). "
                  "It shows how much profit the company made in a year with the capital invested by shareholders."),
        "ja-JP": ("ROE（自己資本利益率）",
                  "当期純利益を自己資本で割った比率です。ROE = 当期純利益 ÷ 自己資本 × 100（%）。"
                  "株主が投資した資本で会社が1年間にどれだけ利益を上げたかを示します。"),
        "zh-CN": ("ROE（净资产收益率）",
                  "当期净利润除以股东权益的比率。ROE = 当期净利润 ÷ 股东权益 × 100（%）。"
                  "表示公司用股东投入的资本在一年内创造了多少利润。"),
    },
    "ROA(총자산이익률)": {
        "en-US": ("ROA (return on assets)",
                  "Net income divided by total assets. ROA = net income ÷ total assets × 100(%). "
                  "It shows how much profit was made with all assets, including liabilities."),
        "ja-JP": ("ROA（総資産利益率）",
                  "当期純利益を総資産で割った比率です。ROA = 当期純利益 ÷ 総資産 × 100（%）。"
                  "負債を含む資産全体でどれだけ利益を上げたかを示します。"),
        "zh-CN": ("ROA（总资产收益率）",
                  "当期净利润除以总资产的比率。ROA = 当期净利润 ÷ 总资产 × 100（%）。"
                  "表示包括负债在内的全部资产创造了多少利润。"),
    },
    "EPS(주당순이익)": {
        "en-US": ("EPS (earnings per share)",
                  "Net income divided by the number of shares issued. EPS = net income ÷ number of shares (usually the average shares outstanding during the period). "
                  "It means the net income earned by one share over a year and is used to calculate PER."),
        "ja-JP": ("EPS（1株当たり純利益）",
                  "当期純利益を発行済株式数で割った値です。EPS = 当期純利益 ÷ 発行済株式数（通常は期中平均の流通株式数）。"
                  "株式1株が1年間に稼いだ純利益を意味し、PERの計算に使われます。"),
        "zh-CN": ("EPS（每股收益）",
                  "当期净利润除以已发行股数得到的值。EPS = 当期净利润 ÷ 已发行股数（通常为期间平均流通股数）。"
                  "表示每一股在一年内赚取的净利润，用于计算PER。"),
    },
    "BPS(주당순자산)": {
        "en-US": ("BPS (book value per share)",
                  "Net assets (assets - liabilities) divided by the number of shares issued. BPS = net assets ÷ number of shares. It is used to calculate PBR."),
        "ja-JP": ("BPS（1株当たり純資産）",
                  "純資産（資産 - 負債）を発行済株式数で割った値です。BPS = 純資産 ÷ 発行済株式数。PBRの計算に使われます。"),
        "zh-CN": ("BPS（每股净资产）",
                  "净资产（资产 - 负债）除以已发行股数得到的值。BPS = 净资产 ÷ 已发行股数。用于计算PBR。"),
    },
    "배당수익률": {
        "en-US": ("Dividend yield",
                  "The dividend per share as a ratio of the share price. Dividend yield = dividend per share ÷ share price × 100(%). "
                  "For example, if the share price is KRW 10,000 and the dividend per share is KRW 500, the dividend yield is 5%. "
                  "The value differs depending on which price (purchase price, current price, etc.) is used."),
        "ja-JP": ("配当利回り",
                  "株価に対する1株当たり配当金の比率です。配当利回り = 1株当たり配当金 ÷ 株価 × 100（%）。"
                  "例えば株価が10,000ウォンで1株当たり配当金が500ウォンなら、配当利回りは5%です。"
                  "基準とする株価（購入価格、現在値など）によって値が変わります。"),
        "zh-CN": ("股息率",
                  "每股股息占股价的比率。股息率 = 每股股息 ÷ 股价 × 100（%）。"
                  "例如股价为10,000韩元、每股股息为500韩元时，股息率为5%。"
                  "所依据的股价（买入价、现价等）不同，数值也会不同。"),
    },
    "배당성향": {
        "en-US": ("Dividend payout ratio",
                  "The share of net income paid out as dividends. Dividend payout ratio = total dividends ÷ net income × 100(%)."),
        "ja-JP": ("配当性向", "当期純利益のうち配当金として支払った比率です。配当性向 = 配当金総額 ÷ 当期純利益 × 100（%）。"),
        "zh-CN": ("股息支付率", "当期净利润中作为股息支付的比率。股息支付率 = 股息总额 ÷ 当期净利润 × 100（%）。"),
    },
    "배당락": {
        "en-US": ("Ex-dividend",
                  "Losing the right to receive a dividend. Dividends go to shareholders listed in the shareholder register on the record date; "
                  "since Korean stocks settle 2 business days after purchase, shares bought from the business day before the record date "
                  "(the ex-dividend date) do not receive that dividend. On the ex-dividend date, the share price may open adjusted by the dividend value."),
        "ja-JP": ("配当落ち",
                  "配当を受け取る権利がなくなることです。配当は配当基準日に株主名簿に載っている株主が受け取りますが、韓国の株式は購入後"
                  "2営業日後に決済されるため、基準日の直前の営業日（配当落ち日）から買った株式はその回の配当を受け取れません。"
                  "配当落ち日には配当の価値分だけ株価が調整されて始まることがあります。"),
        "zh-CN": ("除息",
                  "失去领取股息的权利。股息由股权登记日列在股东名册上的股东领取；韩国股票在买入后2个工作日结算，"
                  "因此从登记日前一个工作日（除息日）起买入的股票领不到该期股息。除息日股价有时会按股息价值调整后开盘。"),
    },
    "공매도": {
        "en-US": ("Short selling",
                  "A trade in which you borrow shares you do not own, sell them first, and later buy them back to return them. "
                  "Buying back at a price lower than the selling price yields a profit, while a rising price causes a loss (in theory, the loss has no limit). "
                  "In Korea, naked short selling, which sells shares without borrowing them, is prohibited."),
        "ja-JP": ("空売り",
                  "保有していない株式を借りて先に売り、後で買い戻して返す取引です。"
                  "売った価格より低い価格で買い戻せば差益が生じ、株価が上がると損失が出ます（理論上、損失に上限はありません）。"
                  "韓国では株式を借りずに売る無担保空売り（ネイキッド・ショート）が禁止されています。"),
        "zh-CN": ("卖空",
                  "借入自己并不持有的股票先卖出，之后再买回归还的交易。"
                  "若以低于卖出价的价格买回则获利，股价上涨则亏损（理论上亏损没有上限）。"
                  "韩国禁止不借入股票直接卖出的无券卖空（裸卖空）。"),
    },
    "시가총액": {
        "en-US": ("Market capitalization",
                  "The market value of all shares a company has issued. Market capitalization = share price × number of shares issued. "
                  "Because the share price alone cannot compare company sizes, it is used to compare the size of companies."),
        "ja-JP": ("時価総額",
                  "会社が発行した株式全体の市場価値です。時価総額 = 株価 × 発行済株式数。"
                  "株価だけでは会社の規模を比較できないため、企業規模を比較するときに使います。"),
        "zh-CN": ("总市值",
                  "公司已发行全部股票的市场价值。总市值 = 股价 × 已发行股数。仅凭股价无法比较公司规模，因此用于比较企业规模。"),
    },
    "공모주": {
        "en-US": ("IPO shares",
                  "Shares a company sells to general investors through a public offering when it lists (IPO) on the stock market. "
                  "Investors subscribe through a brokerage and are allocated shares depending on the competition ratio and other factors."),
        "ja-JP": ("公募株",
                  "企業が証券市場に上場（IPO）する際に、一般投資家に公募で販売する株式です。"
                  "投資家は証券会社を通じて申し込み、競争率などに応じて株式の割り当てを受けます。"),
        "zh-CN": ("新股（公开发行股）",
                  "企业在证券市场上市（IPO）时，通过公开募集向一般投资者出售的股票。投资者通过证券公司认购，并根据中签率等获得配售。"),
    },
    "IPO(기업공개)": {
        "en-US": ("IPO (initial public offering)",
                  "When an unlisted company sells shares publicly to general investors and lists on the stock market. "
                  "The shares sold to general investors at this time are called IPO shares."),
        "ja-JP": ("IPO（新規株式公開）",
                  "非上場企業が株式を一般投資家に公開して販売し、証券市場に上場することです。このとき一般投資家に販売する株式を公募株といいます。"),
        "zh-CN": ("IPO（首次公开发行）",
                  "非上市企业向一般投资者公开出售股票并在证券市场上市。此时向一般投资者出售的股票称为新股。"),
    },
    "ETF(상장지수펀드)": {
        "en-US": ("ETF (exchange-traded fund)",
                  "A fund designed to track the movement of a specific index or asset price, listed on an exchange so it can be bought and sold like a stock. "
                  "A single product gives the effect of investing across many stocks, and it charges management fees."),
        "ja-JP": ("ETF（上場投資信託）",
                  "特定の指数や資産価格の動きに連動するように設計されたファンドで、取引所に上場しており株式のように売買できます。"
                  "1つの商品で複数の銘柄に分散投資する効果があり、運用報酬がかかります。"),
        "zh-CN": ("ETF（交易所交易基金）",
                  "设计为跟踪特定指数或资产价格走势的基金，在交易所上市，可以像股票一样买卖。一个产品即可达到分散投资多只股票的效果，并收取管理费。"),
    },
    "레버리지 ETF": {
        "en-US": ("Leveraged ETF",
                  "An ETF designed to track a fixed multiple (for example, 2x) of the underlying index's daily return. "
                  "Because the multiple is reset daily, holding it for several days can make its return differ from that multiple of the index's return over the same period "
                  "due to compounding, and losses can grow in a market that repeatedly rises and falls."),
        "ja-JP": ("レバレッジETF",
                  "基礎指数の1日の収益率の一定倍率（例えば2倍）に連動するように設計されたETFです。"
                  "1日単位で倍率を合わせるため、複数日保有すると複利効果により同じ期間の基礎指数収益率の倍率と異なる場合があり、"
                  "上下を繰り返す相場では損失が大きくなることがあります。"),
        "zh-CN": ("杠杆ETF",
                  "设计为跟踪基础指数单日收益率一定倍数（例如2倍）的ETF。由于按日调整倍数，持有多日时受复利影响，"
                  "可能与同期基础指数收益率的倍数不同，在反复涨跌的行情中亏损可能扩大。"),
    },
    "인버스 ETF": {
        "en-US": ("Inverse ETF",
                  "An ETF designed to track the opposite direction (for example, -1x) of the underlying index's daily return, so it gains when the index falls. "
                  "Because it is reset daily, holding it for several days can make its return differ from the opposite of the index's return over the same period."),
        "ja-JP": ("インバースETF",
                  "基礎指数の1日の収益率と反対方向（例えば-1倍）に連動するように設計されたETFで、指数が下がると収益が出ます。"
                  "1日単位で合わせるため、複数日保有すると同じ期間の基礎指数収益率の反対と異なる場合があります。"),
        "zh-CN": ("反向ETF",
                  "设计为跟踪基础指数单日收益率反方向（例如-1倍）的ETF，指数下跌时获利。由于按日调整，持有多日时可能与同期基础指数收益率的反向不同。"),
    },
    "거래량": {
        "en-US": ("Trading volume",
                  "The number of shares traded during a given period (usually one day). If the same share is bought and sold several times, every trade is added."),
        "ja-JP": ("出来高", "一定期間（通常は1日）に約定した株式の数です。同じ株式が何度も売買されれば、その分すべて合算されます。"),
        "zh-CN": ("成交量", "一定期间（通常为一天）内成交的股票数量。同一股票被多次买卖时，每次都会计入。"),
    },
    "거래대금": {
        "en-US": ("Trading value",
                  "The total amount of trades executed during a given period (the sum of execution price × executed quantity). "
                  "Even with the same volume, a higher share price means a larger trading value."),
        "ja-JP": ("売買代金", "一定期間に約定した取引の金額の合計です（約定価格 × 約定数量の合計）。出来高が同じでも株価が高ければ売買代金は大きくなります。"),
        "zh-CN": ("成交额", "一定期间内成交交易的金额合计（成交价格 × 成交数量之和）。成交量相同时，股价越高成交额越大。"),
    },
    "등락률": {
        "en-US": ("Rate of change",
                  "The rate at which the price rose or fell compared with a reference price. The daily rate of change is usually based on the previous day's close: "
                  "rate of change = (current price - previous close) ÷ previous close × 100(%)."),
        "ja-JP": ("騰落率", "基準価格に対して価格が上がったり下がったりした比率です。1日の騰落率は通常、前日終値を基準にします：騰落率 = (現在値 - 前日終値) ÷ 前日終値 × 100（%）。"),
        "zh-CN": ("涨跌幅", "相对基准价格上涨或下跌的比率。单日涨跌幅通常以前一交易日收盘价为基准：涨跌幅 = (现价 - 前收盘价) ÷ 前收盘价 × 100（%）。"),
    },
    "신용융자(신용거래융자)": {
        "en-US": ("Margin loan",
                  "Buying stocks with money borrowed from a brokerage. Interest is charged on the borrowed money, and if the share price falls and the collateral ratio "
                  "drops below the requirement, you may be asked for additional collateral or the brokerage may forcibly sell the shares (forced liquidation)."),
        "ja-JP": ("信用取引融資",
                  "証券会社からお金を借りて株式を買うことです。借りたお金には利息がかかり、株価が下がって担保比率が基準を下回ると、"
                  "追加担保を求められたり証券会社が株式を強制的に売却したりすることがあります（反対売買）。"),
        "zh-CN": ("融资（信用交易融资）",
                  "向证券公司借钱买入股票。借款需支付利息；若股价下跌导致担保比例低于标准，可能被要求追加担保，或由证券公司强制卖出股票（强制平仓）。"),
    },
    "반대매매": {
        "en-US": ("Forced liquidation",
                  "When an investor fails to repay borrowed money or unpaid amounts within the set period, or the collateral ratio drops below the requirement, "
                  "the brokerage forcibly sells the investor's shares to recover the money."),
        "ja-JP": ("反対売買",
                  "投資家が借りたお金や未収金を決められた期間内に返済できなかったり、担保比率が基準を下回ったりしたときに、"
                  "証券会社が投資家の株式を強制的に売却して資金を回収することです。"),
        "zh-CN": ("强制平仓", "投资者未能在规定期限内偿还借款或欠款，或担保比例低于标准时，证券公司强制卖出投资者的股票以收回资金。"),
    },
    "상한가": {
        "en-US": ("Upper price limit",
                  "The highest price a stock can rise to in a day. The daily price limit in the Korean stock market is ±30% of the base price "
                  "(usually the previous close), and the upper limit is the price corresponding to +30%."),
        "ja-JP": ("ストップ高", "1日に株価が上がることのできる最高価格です。韓国株式市場の1日の値幅制限は基準価格（通常は前日終値）の±30%で、ストップ高は+30%に当たる価格です。"),
        "zh-CN": ("涨停价", "股价在一天内可以上涨到的最高价格。韩国股市的单日价格限制为基准价（通常为前收盘价）的±30%，涨停价为对应+30%的价格。"),
    },
    "하한가": {
        "en-US": ("Lower price limit",
                  "The lowest price a stock can fall to in a day. The price limit in the Korean stock market is ±30% of the base price, "
                  "and the lower limit is the price corresponding to -30%."),
        "ja-JP": ("ストップ安", "1日に株価が下がることのできる最低価格です。韓国株式市場の値幅制限は基準価格の±30%で、ストップ安は-30%に当たる価格です。"),
        "zh-CN": ("跌停价", "股价在一天内可以下跌到的最低价格。韩国股市的价格限制为基准价的±30%，跌停价为对应-30%的价格。"),
    },
    "유상증자": {
        "en-US": ("Paid-in capital increase (rights offering)",
                  "A company increases its capital by issuing new shares and selling them to investors for money. "
                  "Because the number of shares increases, the ownership stake of existing shareholders who do not participate may fall."),
        "ja-JP": ("有償増資", "会社が新株を発行して投資家からお金を受け取って販売する方法で資本金を増やすことです。株式数が増えるため、参加しなかった既存株主の持株比率は下がる可能性があります。"),
        "zh-CN": ("有偿增资（配股/增发）", "公司发行新股并向投资者收取资金出售，以此增加资本金。由于股数增加，未参与的现有股东持股比例可能下降。"),
    },
    "무상증자": {
        "en-US": ("Bonus issue (free capital increase)",
                  "A company issues new shares without receiving money and distributes them to existing shareholders in proportion to their holdings. "
                  "It is an accounting entry that moves internal surplus into capital, so the company's total value stays the same, "
                  "and the share price is adjusted for the increased number of shares (ex-rights)."),
        "ja-JP": ("無償増資",
                  "会社がお金を受け取らずに新株を発行し、既存株主に保有比率に応じて配ることです。"
                  "会社内部の剰余金を資本金に移す会計処理のため会社全体の価値は変わらず、増えた株式数の分だけ株価が調整されます（権利落ち）。"),
        "zh-CN": ("无偿增资（送股）",
                  "公司不收取资金而发行新股，并按持股比例分配给现有股东。这是将公司内部盈余转为资本金的会计处理，"
                  "公司整体价值不变，股价会按增加的股数进行调整（除权）。"),
    },
    "자사주 매입(자기주식 취득)": {
        "en-US": ("Share buyback (acquisition of treasury shares)",
                  "A company buying back its own shares. The number of shares circulating in the market decreases, and if the repurchased shares are retired, "
                  "the number of shares issued itself decreases."),
        "ja-JP": ("自社株買い（自己株式の取得）", "会社が自社の株式を買い入れることです。市場に出回る株式数が減り、買い入れた株式を消却すると発行済株式数そのものが減ります。"),
        "zh-CN": ("股票回购（取得库存股）", "公司买入本公司的股票。市场上流通的股数减少，若注销回购的股票，已发行股数本身也会减少。"),
    },
    "액면분할": {
        "en-US": ("Stock split",
                  "Dividing one share into several shares (lowering the par value per share). The number of shares increases and the price per share falls accordingly, "
                  "while market capitalization and shareholders' ownership stakes do not change."),
        "ja-JP": ("株式分割", "株式1株を複数の株に分けることです（1株の額面を下げる）。株式数は増え、1株当たりの価格はその分低くなり、時価総額と株主の持株比率は変わりません。"),
        "zh-CN": ("拆股（面值分割）", "将1股拆分为多股（降低每股面值）。股数增加，每股价格相应降低，总市值和股东持股比例不变。"),
    },
    "우선주": {
        "en-US": ("Preferred stock",
                  "Stock that has priority over common stock in dividends and other matters, but has no or limited voting rights. "
                  "In Korea, preferred stocks are often shown with '우' added after the stock name (it is a different stock from the common stock)."),
        "ja-JP": ("優先株", "普通株より配当などで優先権を持つ代わりに、議決権がないか制限される株式です。韓国の優先株は銘柄名の後に「우」を付けて表示されることが多いです（普通株とは別の銘柄です）。"),
        "zh-CN": ("优先股", "在股息等方面比普通股享有优先权，但没有或仅有有限表决权的股票。韩国的优先股通常在股票名称后加“우”表示（与普通股是不同的股票）。"),
    },
    "코스피(KOSPI)": {
        "en-US": ("KOSPI",
                  "An index that shows the total market capitalization of all stocks listed on the KOSPI market compared with a base point. "
                  "It is calculated by setting the market capitalization of January 4, 1980 to 100. 'KOSPI' is also used to refer to the KOSPI market itself."),
        "ja-JP": ("KOSPI（韓国総合株価指数）", "有価証券市場に上場している全銘柄の時価総額を基準時点と比較して示した指数です。1980年1月4日の時価総額を100として計算します。「KOSPI」は有価証券市場そのものを指す言葉としても使われます。"),
        "zh-CN": ("KOSPI（韩国综合股价指数）", "将有价证券市场上市的全部股票总市值与基准时点相比得到的指数。以1980年1月4日的总市值为100计算。“KOSPI”也用来指有价证券市场本身。"),
    },
    "코스닥(KOSDAQ)": {
        "en-US": ("KOSDAQ",
                  "An index that shows the total market capitalization of all stocks listed on the KOSDAQ market (a stock market centered on technology and growth companies) "
                  "compared with a base point. 'KOSDAQ' is also used to refer to that market itself."),
        "ja-JP": ("KOSDAQ（コスダック）", "KOSDAQ市場（技術・成長企業中心の株式市場）に上場している全銘柄の時価総額を基準時点と比較して示した指数です。「KOSDAQ」はその市場そのものを指す言葉としても使われます。"),
        "zh-CN": ("KOSDAQ（科斯达克）", "将KOSDAQ市场（以技术和成长型企业为主的股票市场）上市的全部股票总市值与基准时点相比得到的指数。“KOSDAQ”也用来指该市场本身。"),
    },
    "영업이익": {
        "en-US": ("Operating profit",
                  "Profit calculated by subtracting cost of sales and selling, general and administrative expenses from revenue. It shows the profit the company earned from its core business."),
        "ja-JP": ("営業利益", "売上高から売上原価と販売費・一般管理費を差し引いた利益です。会社が本業で稼いだ利益を示します。"),
        "zh-CN": ("营业利润", "营业收入减去营业成本以及销售费用和管理费用后的利润。表示公司通过主营业务获得的利润。"),
    },
    "종가": {
        "en-US": ("Closing price",
                  "The last price determined at the end of a day's regular trading. For Korean stocks, it is set by collecting orders for a certain period before the close "
                  "and executing them at once. The daily rate of change is usually calculated against the previous day's close."),
        "ja-JP": ("終値", "1日の通常取引が終わるときに最後に決まった価格です。韓国の株式は取引終了前の一定時間に注文を集めて一度に約定させる方式で決まります。1日の騰落率は通常、前日終値と比較して計算します。"),
        "zh-CN": ("收盘价", "一天常规交易结束时最后确定的价格。韩国股票通过在收盘前一段时间集中订单并一次性成交的方式确定。单日涨跌幅通常与前一交易日收盘价比较计算。"),
    },
    "환율": {
        "en-US": ("Exchange rate",
                  "The rate at which one country's currency is exchanged for another's. The KRW/USD exchange rate is the amount of won needed to buy 1 dollar, "
                  "so when the exchange rate rises, the value of the won has fallen."),
        "ja-JP": ("為替レート", "ある国のお金と別の国のお金を交換する比率です。ウォン/ドルの為替レートは1ドルを買うのに必要なウォンの金額なので、為替レートが上がるとウォンの価値は下がったことになります。"),
        "zh-CN": ("汇率", "一个国家的货币与另一个国家的货币兑换的比率。韩元/美元汇率是购买1美元所需的韩元金额，因此汇率上升意味着韩元价值下降。"),
    },
}

FOOTNOTE = {
    "en-US": "※ This is a general explanation from the built-in glossary. It is not a figure for a specific stock or an investment recommendation.",
    "ja-JP": "※ 内蔵の用語辞典による一般的な説明です。特定銘柄の数値や投資勧誘ではありません。",
    "zh-CN": "※ 这是内置术语词典的一般性说明，并非特定股票的数据或投资劝诱。",
}
INTERPRETATION_NOTE = {
    "en-US": ("However, it is hard to judge whether a stock is cheap or expensive, or good or bad, from this one indicator alone. "
              "It is common to compare it with other companies in the same industry and look at it together with other information such as profit trends and financial condition."),
    "ja-JP": ("ただし、この指標1つだけで株価が割安・割高、または良い・悪いと判断するのは難しいです。"
              "同じ業種の他社と比較し、利益の推移や財務状態などの他の情報とあわせて見るのが一般的です。"),
    "zh-CN": "不过，仅凭这一个指标很难判断股价便宜或昂贵、好或坏。通常会与同行业其他公司比较，并结合利润趋势、财务状况等其他信息一起看。",
}
UNKNOWN = {
    "en-US": "'{word}' is not in the built-in glossary, so we can't give an exact definition. Terms in the glossary: {terms}.",
    "ja-JP": "「{word}」は内蔵の用語辞典にない用語のため、正確な定義をお伝えできません。辞典にある用語：{terms}。",
    "zh-CN": "“{word}”不在内置术语词典中，无法给出准确定义。词典中的术语：{terms}。",
}
_LIST_SEP = {"en-US": ", ", "ja-JP": "、", "zh-CN": "、"}


def _head(term: str, locale: str) -> str:
    return DEFINITIONS[term][locale][0]


def term_answer(term: str, locale: str, interpretation: bool = False) -> str:
    parts = [f"**{_head(term, locale)}**\n{DEFINITIONS[term][locale][1]}"]
    if interpretation:
        parts.append(INTERPRETATION_NOTE[locale])
    parts.append(FOOTNOTE[locale])
    return "\n\n".join(parts)


def comparison_answer(terms: list[str], locale: str) -> str:
    return "\n\n".join([f"**{_head(t, locale)}**\n{DEFINITIONS[t][locale][1]}" for t in terms] + [FOOTNOTE[locale]])


def term_list(locale: str) -> list[str]:
    """사전 표제어의 짧은 이름(그 언어). app.glossary.term_list와 같은 순서."""
    return [_head(t, locale).split(" (")[0].split("（")[0] for t in g.GLOSSARY]


def concept_answer(question: str, locale: str) -> str | None:
    """app.glossary.concept_answer와 같은 판정, 답만 그 언어로. 판정 규칙은 glossary의 정규식을 그대로 쓴다."""
    q = (question or "").strip()
    if not q:
        return None
    if not g._CONTEXT_RE.search(q):
        terms = g.find_terms(q)
        if len(terms) >= 2 and g._COMPARE_RE.search(q):
            return comparison_answer(terms[:3], locale)
        if len(terms) == 1 and g._INTERPRET_RE.search(q):
            return term_answer(terms[0], locale, interpretation=True)
    if not g._DEFINITION_RE.search(q) or g._NOT_DEFINITION_RE.search(q) or g._JUDGMENT_RE.search(q):
        return None
    term = g.find_term(q)
    if term is not None:
        return term_answer(term, locale)
    m = g._UNKNOWN_TERM_RE.match(q)
    if m is None:
        return None
    import re

    word = re.sub(r"(?:이|가|은|는|란|이란)$", "", m.group("term")).strip()
    return UNKNOWN[locale].format(word=word, terms=_LIST_SEP[locale].join(term_list(locale)))
