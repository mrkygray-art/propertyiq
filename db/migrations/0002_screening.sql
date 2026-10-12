-- Tier 1 loan screening: lender guideline sets.
-- Separate from the portfolio tables. The API writes here through piq_screening_writer,
-- which can change only this table (see scripts/screening_setup.py).

CREATE TABLE screening_guideline_sets (
    set_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name        text NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 80),
    -- Rule settings as JSON. Thresholds are stored as decimal strings; percentages as fractions.
    rules       jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

INSERT INTO screening_guideline_sets (name, rules) VALUES (
    'Example (set your own)',
    '{"min_dscr": {"enabled": true, "threshold": "1.25"},
      "max_ltv": {"enabled": true, "threshold": "0.70"},
      "min_debt_yield": {"enabled": true, "threshold": "0.08"},
      "max_break_even": {"enabled": true, "threshold": "0.85"},
      "min_loan": {"enabled": false, "threshold": null},
      "max_loan": {"enabled": false, "threshold": null},
      "allowed_types": {"enabled": false, "types": ["multifamily", "office", "retail", "industrial", "mixed_use", "other"]},
      "dscr_basis": "amortizing"}'
);
