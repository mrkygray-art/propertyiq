-- PropertyIQ core schema. All data in this database is synthetic.
-- Amounts are numeric(14,2) dollars. Occupancy is physical: occupied size / rentable size
-- (square feet for commercial assets, units for multifamily) as of the month end.

CREATE TABLE portfolios (
    portfolio_id  text PRIMARY KEY,
    name          text NOT NULL UNIQUE,
    is_synthetic  boolean NOT NULL DEFAULT true CHECK (is_synthetic)
);

CREATE TABLE properties (
    property_id   text PRIMARY KEY CHECK (property_id ~ '^PIQ-[0-9]{3}$'),
    portfolio_id  text NOT NULL REFERENCES portfolios,
    name          text NOT NULL UNIQUE,
    asset_class   text NOT NULL CHECK (asset_class IN ('office', 'retail', 'industrial', 'multifamily')),
    market        text NOT NULL,
    size_unit     text NOT NULL CHECK (size_unit IN ('sf', 'units')),
    rentable_size integer NOT NULL CHECK (rentable_size > 0),
    is_synthetic  boolean NOT NULL DEFAULT true CHECK (is_synthetic),
    CHECK ((asset_class = 'multifamily') = (size_unit = 'units'))
);
CREATE INDEX properties_portfolio_idx ON properties (portfolio_id);

-- One row per property per month of actual operating results.
CREATE TABLE monthly_financials (
    record_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    property_id        text NOT NULL REFERENCES properties,
    month_end          date NOT NULL
        CHECK (month_end = (date_trunc('month', month_end) + interval '1 month - 1 day')::date),
    operating_revenue  numeric(14,2) NOT NULL,
    operating_expenses numeric(14,2) NOT NULL,
    -- NOI excludes financing costs and capital expenditures, which are not stored here.
    noi                numeric(14,2) GENERATED ALWAYS AS (operating_revenue - operating_expenses) STORED,
    occupied_size      integer NOT NULL CHECK (occupied_size >= 0),
    rentable_size      integer NOT NULL CHECK (rentable_size > 0),
    CHECK (occupied_size <= rentable_size),
    UNIQUE (property_id, month_end)
);

-- One row per property per month of the approved operating budget.
CREATE TABLE monthly_budgets (
    record_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    property_id      text NOT NULL REFERENCES properties,
    month_end        date NOT NULL
        CHECK (month_end = (date_trunc('month', month_end) + interval '1 month - 1 day')::date),
    budget_revenue   numeric(14,2) NOT NULL,
    budget_expenses  numeric(14,2) NOT NULL,
    budget_noi       numeric(14,2) GENERATED ALWAYS AS (budget_revenue - budget_expenses) STORED,
    UNIQUE (property_id, month_end)
);

-- Demo sign-in only. Not production identity management.
CREATE TABLE demo_users (
    username       text PRIMARY KEY,
    display_name   text NOT NULL,
    role           text NOT NULL CHECK (role IN ('analyst', 'restricted_analyst')),
    password_hash  text NOT NULL
);

-- Authorization: a user may see a property only through a portfolio grant.
CREATE TABLE user_portfolio_access (
    username      text NOT NULL REFERENCES demo_users ON DELETE CASCADE,
    portfolio_id  text NOT NULL REFERENCES portfolios ON DELETE CASCADE,
    PRIMARY KEY (username, portfolio_id)
);

-- Records what the seed run produced, so tests and the UI can show provenance.
CREATE TABLE seed_runs (
    seed_run_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    generator       text NOT NULL,
    random_seed     bigint NOT NULL,
    content_sha256  text NOT NULL,
    property_count  integer NOT NULL,
    loaded_at       timestamptz NOT NULL DEFAULT now()
);
