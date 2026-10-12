-- Synthetic lending data for the debt dashboard. Every row is synthetic; loans belong to one fund and are
-- shown only to users granted that fund (same rule as properties). Balances, debt service, DSCR, LTV and
-- debt yield are not stored: they are computed from these terms by propertyiq/lending/calc.py.

CREATE TABLE lenders (
    lender_id     text PRIMARY KEY,
    name          text NOT NULL UNIQUE,
    is_synthetic  boolean NOT NULL DEFAULT true CHECK (is_synthetic)
);

CREATE TABLE loans (
    loan_id             text PRIMARY KEY CHECK (loan_id ~ '^LN-[0-9]{4}$'),
    fund_id             text NOT NULL REFERENCES portfolios ON DELETE CASCADE,
    lender_id           text NOT NULL REFERENCES lenders,
    name                text NOT NULL,
    original_principal  numeric(14,2) NOT NULL CHECK (original_principal > 0),
    rate_type           text NOT NULL CHECK (rate_type IN ('fixed', 'floating')),
    fixed_rate_pct      numeric(7,4) CHECK (fixed_rate_pct > 0),
    index_name          text,
    spread_bps          integer CHECK (spread_bps >= 0),
    amortization_months integer NOT NULL CHECK (amortization_months >= 0),  -- 0 = interest only
    io_months           integer NOT NULL DEFAULT 0 CHECK (io_months >= 0),
    origination_date    date NOT NULL,
    maturity_date       date NOT NULL CHECK (maturity_date > origination_date),
    is_synthetic        boolean NOT NULL DEFAULT true CHECK (is_synthetic),
    CHECK ((rate_type = 'fixed') = (fixed_rate_pct IS NOT NULL)),
    CHECK ((rate_type = 'floating') = (index_name IS NOT NULL AND spread_bps IS NOT NULL))
);

-- Many-to-many: a loan can be secured by several properties, and a property's value and NOI can be split
-- between loans. allocation_pct is the share of the property's NOI and value counted for this loan.
CREATE TABLE loan_collateral (
    loan_id         text NOT NULL REFERENCES loans ON DELETE CASCADE,
    property_id     text NOT NULL REFERENCES properties ON DELETE CASCADE,
    allocation_pct  numeric(7,4) NOT NULL CHECK (allocation_pct > 0 AND allocation_pct <= 100),
    PRIMARY KEY (loan_id, property_id)
);

CREATE TABLE loan_covenants (
    covenant_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    loan_id         text NOT NULL REFERENCES loans ON DELETE CASCADE,
    kind            text NOT NULL CHECK (kind IN ('min_dscr', 'max_ltv', 'min_debt_yield')),
    threshold       numeric(9,4) NOT NULL CHECK (threshold > 0),
    noi_definition  text NOT NULL DEFAULT 'Trailing 12 months actual NOI of the collateral, times its allocation',
    UNIQUE (loan_id, kind)
);

CREATE TABLE rate_caps (
    loan_id         text PRIMARY KEY REFERENCES loans ON DELETE CASCADE,
    strike_pct      numeric(7,4) NOT NULL CHECK (strike_pct > 0),
    expiry_date     date NOT NULL
);

-- Monthly index fixings for floating loans (synthetic, not a real published rate).
CREATE TABLE index_rates (
    record_id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    index_name  text NOT NULL,
    month_end   date NOT NULL,
    rate_pct    numeric(7,4) NOT NULL,
    UNIQUE (index_name, month_end)
);

CREATE TABLE property_valuations (
    valuation_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    property_id   text NOT NULL REFERENCES properties ON DELETE CASCADE,
    as_of_date    date NOT NULL,
    value         numeric(16,2) NOT NULL CHECK (value > 0),
    method        text NOT NULL,
    cap_rate_pct  numeric(7,4),
    is_synthetic  boolean NOT NULL DEFAULT true CHECK (is_synthetic),
    UNIQUE (property_id, as_of_date)
);

CREATE INDEX loans_fund_idx ON loans (fund_id);
CREATE INDEX loan_collateral_property_idx ON loan_collateral (property_id);
