-- ============================================================
-- AI 투자 리서치 에이전트 - DB 스키마 (MVP)
-- PostgreSQL + pgvector
-- 대상 FR: FR-01, 02, 03, 04, 05, 08, 09
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ------------------------------------------------------------
-- 1. COMPANY (기업 정보)
-- ------------------------------------------------------------
CREATE TABLE company (
    id          SERIAL PRIMARY KEY,
    ticker      VARCHAR(10) NOT NULL UNIQUE,
    name        VARCHAR(100) NOT NULL,
    market      VARCHAR(20),
    sector      VARCHAR(50),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 2. STOCK_PRICE (주가·거래량 - FR-02)
-- ------------------------------------------------------------
CREATE TABLE stock_price (
    id           BIGSERIAL PRIMARY KEY,
    company_id   INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    price_date   DATE NOT NULL,
    open_price   NUMERIC(12, 2),
    high_price   NUMERIC(12, 2),
    low_price    NUMERIC(12, 2),
    close_price  NUMERIC(12, 2) NOT NULL,
    volume       BIGINT,
    change_pct   NUMERIC(6, 2),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, price_date)
);

CREATE INDEX idx_stock_price_company_date
    ON stock_price (company_id, price_date DESC);

-- ------------------------------------------------------------
-- 3. NEWS (뉴스 - FR-03, RAG 근거용 임베딩 포함 - FR-08)
-- ------------------------------------------------------------
CREATE TABLE news (
    id            BIGSERIAL PRIMARY KEY,
    company_id    INTEGER REFERENCES company(id) ON DELETE SET NULL,
    title         TEXT NOT NULL,
    content       TEXT,
    source        VARCHAR(100),
    url           TEXT,
    published_at  TIMESTAMPTZ,
    embedding     vector(1024),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_news_company_published
    ON news (company_id, published_at DESC);

-- ------------------------------------------------------------
-- 4. DISCLOSURE (기업 공시 - FR-04, RAG 근거용 임베딩 포함 - FR-08)
-- ------------------------------------------------------------
CREATE TABLE disclosure (
    id                BIGSERIAL PRIMARY KEY,
    company_id        INTEGER NOT NULL REFERENCES company(id) ON DELETE CASCADE,
    title             TEXT NOT NULL,
    disclosure_type   VARCHAR(50),
    content           TEXT,
    source_url        TEXT,
    disclosed_at      TIMESTAMPTZ,
    embedding         vector(1024),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_disclosure_company_date
    ON disclosure (company_id, disclosed_at DESC);

-- ------------------------------------------------------------
-- 5. RESEARCH_REPORT (AI 리서치 보고서 - FR-05, FR-11 대비)
-- ------------------------------------------------------------
CREATE TABLE research_report (
    id           BIGSERIAL PRIMARY KEY,
    company_id   INTEGER REFERENCES company(id) ON DELETE SET NULL,
    question     TEXT NOT NULL,
    summary      TEXT,
    content      TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------
-- 6. TOOL_CALL_LOG (Agent Tool Calling 이력 - FR-09, 디버깅/분석용)
-- ------------------------------------------------------------
CREATE TABLE tool_call_log (
    id           BIGSERIAL PRIMARY KEY,
    report_id    BIGINT NOT NULL REFERENCES research_report(id) ON DELETE CASCADE,
    tool_name    VARCHAR(50) NOT NULL,
    arguments    JSONB,
    result       JSONB,
    called_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tool_call_log_report ON tool_call_log (report_id);

-- ============================================================
-- 참고 (지금은 만들지 않음, FR-06/07 확장 시 고려)
-- - industry_index (산업 지표), economic_indicator (금리/환율 등)
-- - 지금 스코프에서는 company.sector만으로 임시 대체
-- ============================================================