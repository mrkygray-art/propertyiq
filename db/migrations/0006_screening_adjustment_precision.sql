-- Adjustment values hold both dollars and rates (e.g. a vacancy of 0.10531915). Four decimal places
-- rounded rates enough to move NOI by about $10 on a 20-unit deal, so keep more precision.
ALTER TABLE screening_adjustments
    ALTER COLUMN borrower_value TYPE numeric(24, 10),
    ALTER COLUMN underwritten_value TYPE numeric(24, 10);
