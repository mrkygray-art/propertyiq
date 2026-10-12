-- Tier 2: document intake, flags, benchmarks, market cache, California rules, condition checklist,
-- borrower vs underwritten adjustments. New tables only; nothing existing is altered.

-- A deal ties together the Tier 1 inputs (borrower case), uploaded documents, flags and adjustments.
CREATE TABLE screening_deals (
    deal_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name             text NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    guideline_set_id bigint REFERENCES screening_guideline_sets ON DELETE SET NULL,
    borrower_inputs  jsonb NOT NULL DEFAULT '{}',   -- the Tier 1 deal form as the borrower presented it
    property_facts   jsonb NOT NULL DEFAULT '{}',   -- city, year built, construction, parking, transaction, price...
    created_by       text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE screening_documents (
    document_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deal_id           bigint NOT NULL REFERENCES screening_deals ON DELETE CASCADE,
    kind              text NOT NULL CHECK (kind IN ('rent_roll', 't12')),
    filename          text NOT NULL,
    stored_name       text,                          -- file name under data/uploads; NULL once deleted
    sha256            text NOT NULL,
    size_bytes        integer NOT NULL CHECK (size_bytes >= 0),
    status            text NOT NULL CHECK (status IN ('extracted', 'approved', 'failed', 'deleted')),
    extraction_method text,                          -- excel | csv | pdf_text | pdf_ocr
    error             text,
    summary           jsonb NOT NULL DEFAULT '{}',   -- months found, reconciliation results, warnings
    uploaded_at       timestamptz NOT NULL DEFAULT now(),
    approved_at       timestamptz
);
CREATE INDEX screening_documents_deal_idx ON screening_documents (deal_id);

-- Everything extracted, before approval. Tenant names and contact details are never stored.
CREATE TABLE screening_extracted_items (
    item_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id     bigint NOT NULL REFERENCES screening_documents ON DELETE CASCADE,
    seq             integer NOT NULL,
    item_type       text NOT NULL CHECK (item_type IN ('rent_roll_unit', 't12_line')),
    source_ref      text NOT NULL,                  -- "Sheet 'T12' row 14" / "page 1, line 9"
    original_label  text NOT NULL DEFAULT '',
    category        text,                           -- standard category (T-12) or NULL
    data            jsonb NOT NULL,                 -- normalized values as extracted (or as corrected)
    raw             jsonb NOT NULL DEFAULT '{}',    -- raw cell text, for the reviewer
    confidence      text NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    confidence_note text NOT NULL DEFAULT '',
    mapped_by       text NOT NULL DEFAULT 'synonym',
    edited          boolean NOT NULL DEFAULT false,
    UNIQUE (document_id, seq)
);

-- Approved, normalized data. Rows exist only after a reviewer approves a document.
CREATE TABLE screening_rent_roll_units (
    deal_id        bigint NOT NULL REFERENCES screening_deals ON DELETE CASCADE,
    document_id    bigint NOT NULL REFERENCES screening_documents ON DELETE CASCADE,
    unit_id        text NOT NULL,
    unit_type      text,
    beds           numeric(4,1),
    baths          numeric(4,1),
    sqft           numeric(10,1),
    status         text,
    current_rent   numeric(12,2),
    market_rent    numeric(12,2),
    lease_start    date,
    lease_end      date,
    month_to_month boolean NOT NULL DEFAULT false,
    concessions    numeric(12,2),
    deposit        numeric(12,2),
    source_ref     text NOT NULL
);
CREATE INDEX screening_rent_roll_units_deal_idx ON screening_rent_roll_units (deal_id);

CREATE TABLE screening_t12_lines (
    deal_id        bigint NOT NULL REFERENCES screening_deals ON DELETE CASCADE,
    document_id    bigint NOT NULL REFERENCES screening_documents ON DELETE CASCADE,
    seq            integer NOT NULL,
    original_label text NOT NULL,
    category       text NOT NULL,
    months         jsonb NOT NULL,                 -- {"2025-10": "1234.56", ...}; decimal strings
    reported_total numeric(14,2),
    source_ref     text NOT NULL
);
CREATE INDEX screening_t12_lines_deal_idx ON screening_t12_lines (deal_id);

-- Flags from anomaly checks (A*), market checks (M*) and California red flags (R*).
-- fingerprint keeps a dismissal attached to the same finding when checks are re-run.
CREATE TABLE screening_flags (
    flag_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deal_id          bigint NOT NULL REFERENCES screening_deals ON DELETE CASCADE,
    rule_id          text NOT NULL,
    fingerprint      text NOT NULL,
    severity         text NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
    title            text NOT NULL,
    explanation      text NOT NULL,
    evidence         jsonb NOT NULL DEFAULT '[]',
    sources          jsonb NOT NULL DEFAULT '[]',
    suggested_action text NOT NULL DEFAULT '',
    status           text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'dismissed')),
    dismissal_note   text,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (deal_id, fingerprint)
);

-- Expense benchmarks. Shipped values are examples only; the lender replaces them.
CREATE TABLE screening_benchmarks (
    benchmark_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    property_type  text NOT NULL,
    category       text NOT NULL,
    basis          text NOT NULL CHECK (basis IN ('per_unit', 'per_sf', 'pct_egi')),
    low            numeric(12,4),
    typical        numeric(12,4),
    high           numeric(12,4),
    source         text NOT NULL CHECK (length(source) > 0),
    last_reviewed  date NOT NULL,
    is_example     boolean NOT NULL DEFAULT true,
    UNIQUE (property_type, category, basis)
);

-- Jurisdiction rules (California red flags). Each needs a source URL and a last-verified date,
-- or is marked unverified.
CREATE TABLE screening_rules (
    rule_key       text PRIMARY KEY,
    flag_id        text NOT NULL,              -- R1..R5
    jurisdiction   text NOT NULL,
    name           text NOT NULL,
    trigger        jsonb NOT NULL DEFAULT '{}',
    description    text NOT NULL,
    source_url     text,
    last_verified  date,
    verified       boolean NOT NULL DEFAULT false,
    notes          text NOT NULL DEFAULT ''
);

-- Market data cache (comps), with provider and retrieval date.
CREATE TABLE screening_market_cache (
    cache_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider      text NOT NULL,
    query_key     text NOT NULL,
    query         jsonb NOT NULL,
    results       jsonb NOT NULL,
    retrieved_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, query_key)
);

-- Property condition checklist (deferred maintenance, R5).
CREATE TABLE screening_condition_items (
    deal_id         bigint NOT NULL REFERENCES screening_deals ON DELETE CASCADE,
    item_key        text NOT NULL,
    details         jsonb NOT NULL DEFAULT '{}',   -- type, age, material, brand...
    estimated_cost  numeric(12,2),
    PRIMARY KEY (deal_id, item_key)
);

-- Borrower vs underwritten adjustments. Nothing affects the numbers until approved.
CREATE TABLE screening_adjustments (
    adjustment_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deal_id           bigint NOT NULL REFERENCES screening_deals ON DELETE CASCADE,
    line_item         text NOT NULL,
    borrower_value    numeric(14,4),
    underwritten_value numeric(14,4),
    reason_code       text NOT NULL CHECK (reason_code IN ('MARKET_RENT', 'VACANCY', 'ONE_TIME_ITEM_REMOVED',
                        'EXPENSE_BELOW_BENCHMARK', 'TAX_REASSESSMENT', 'MANAGEMENT_FEE', 'RESERVES',
                        'DEFERRED_MAINTENANCE', 'RENT_CONTROL', 'OTHER')),
    note              text NOT NULL CHECK (length(note) > 0),
    flag_fingerprint  text,
    status            text NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'approved', 'rejected')),
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX screening_adjustments_deal_idx ON screening_adjustments (deal_id);

-- Example benchmarks (multifamily per unit per year; commercial per square foot per year).
-- Placeholders so the checks can run; NOT market data. Replace with your own.
INSERT INTO screening_benchmarks (property_type, category, basis, low, typical, high, source, last_reviewed) VALUES
  ('multifamily', 'insurance',           'per_unit', 450,  700,  1200, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('multifamily', 'repairs_maintenance', 'per_unit', 600,  1000, 1800, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('multifamily', 'utilities',           'per_unit', 700,  1300, 2200, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('multifamily', 'administrative',      'per_unit', 100,  250,  500,  'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('multifamily', 'management_fee',      'pct_egi',  0.03, 0.04, 0.06, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('office',      'insurance',           'per_sf',   0.35, 0.60, 1.00, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('office',      'repairs_maintenance', 'per_sf',   1.00, 1.75, 3.00, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('office',      'utilities',           'per_sf',   1.50, 2.25, 3.50, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('office',      'management_fee',      'pct_egi',  0.025, 0.035, 0.05, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('retail',      'insurance',           'per_sf',   0.30, 0.50, 0.90, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('retail',      'repairs_maintenance', 'per_sf',   0.75, 1.25, 2.25, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('retail',      'management_fee',      'pct_egi',  0.03, 0.04, 0.05, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('industrial',  'insurance',           'per_sf',   0.15, 0.30, 0.60, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('industrial',  'repairs_maintenance', 'per_sf',   0.25, 0.50, 1.00, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('industrial',  'management_fee',      'pct_egi',  0.02, 0.03, 0.04, 'PropertyIQ placeholder: example only, set your own', '2026-10-11');
