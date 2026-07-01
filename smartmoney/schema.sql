-- SmartMoney point-in-time schema.
--
-- Design rules that keep the historical replay honest:
--   * Every filing carries `acceptance_datetime` = the instant it became
--     public on EDGAR. The replay may ONLY read filings whose
--     acceptance_datetime <= the simulated "now". Never the period-of-report.
--   * Raw filings are immutable (append-only). Derived data is rebuildable.
--   * The universe is survivorship-complete: managers that blew up and
--     securities that delisted stay in the DB with their real history.

CREATE TABLE IF NOT EXISTS managers (
    cik            TEXT PRIMARY KEY,       -- SEC Central Index Key
    name           TEXT NOT NULL,
    first_seen     TEXT,                   -- ISO date first appears in data
    active         INTEGER DEFAULT 1       -- 0 if the manager later disappeared
);

CREATE TABLE IF NOT EXISTS securities (
    cusip          TEXT PRIMARY KEY,       -- 13F reports by CUSIP
    ticker         TEXT,                   -- mapped via OpenFIGI
    name           TEXT,
    sector         TEXT,
    delisted_date  TEXT                    -- NULL if still listed
);

-- One row per filing. The heart of point-in-time correctness.
CREATE TABLE IF NOT EXISTS filings (
    accession           TEXT PRIMARY KEY,
    cik                 TEXT NOT NULL,     -- filer (manager) CIK
    form_type           TEXT NOT NULL,     -- 13F-HR, 4, SC 13D, 8-K ...
    period_of_report    TEXT,              -- what the data is ABOUT
    acceptance_datetime TEXT NOT NULL,     -- when it became PUBLIC (the gate)
    filed_date          TEXT,
    FOREIGN KEY (cik) REFERENCES managers(cik)
);
CREATE INDEX IF NOT EXISTS idx_filings_accept ON filings(acceptance_datetime);
CREATE INDEX IF NOT EXISTS idx_filings_form   ON filings(form_type);

-- 13F info-table rows: a manager's position in one security in one filing.
CREATE TABLE IF NOT EXISTS holdings (
    accession      TEXT NOT NULL,
    cusip          TEXT NOT NULL,
    shares         REAL,
    value_usd      REAL,                   -- as reported (x1000 already applied)
    put_call       TEXT,                   -- NULL for common stock
    PRIMARY KEY (accession, cusip, put_call),
    FOREIGN KEY (accession) REFERENCES filings(accession),
    FOREIGN KEY (cusip)     REFERENCES securities(cusip)
);

-- Catalyst events (earnings beats, buybacks, activist 13D) for Step 3.
CREATE TABLE IF NOT EXISTS catalysts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker         TEXT NOT NULL,
    event_date     TEXT NOT NULL,          -- when it became public
    kind           TEXT NOT NULL,          -- earnings_beat | buyback | activist_13d
    detail         TEXT
);
CREATE INDEX IF NOT EXISTS idx_catalysts ON catalysts(ticker, event_date);

-- Daily adjusted closes (survivorship-complete; delisted names keep history).
CREATE TABLE IF NOT EXISTS prices (
    ticker         TEXT NOT NULL,
    date           TEXT NOT NULL,
    close          REAL NOT NULL,          -- split/dividend adjusted
    PRIMARY KEY (ticker, date)
);

-- ---- Replay outputs (rebuilt each run) ----
CREATE TABLE IF NOT EXISTS signals (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    date           TEXT NOT NULL,          -- simulated day signal was formed
    ticker         TEXT NOT NULL,
    conviction     REAL NOT NULL,
    n_managers     INTEGER,
    has_catalyst   INTEGER,
    action         TEXT NOT NULL,          -- BUY | SELL
    reason         TEXT
);

CREATE TABLE IF NOT EXISTS trades (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    date           TEXT NOT NULL,
    ticker         TEXT NOT NULL,
    side           TEXT NOT NULL,          -- BUY | SELL
    shares         REAL,
    price          REAL,
    value          REAL,
    reason         TEXT
);

CREATE TABLE IF NOT EXISTS equity_curve (
    date           TEXT PRIMARY KEY,
    cash           REAL,
    positions_val  REAL,
    total          REAL,
    n_positions    INTEGER
);
