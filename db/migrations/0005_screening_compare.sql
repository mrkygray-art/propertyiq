-- Tier 2 comparison: one approved adjustment per line item per deal, and an example reserve setting.

CREATE UNIQUE INDEX screening_adjustments_one_approved
    ON screening_adjustments (deal_id, line_item) WHERE status = 'approved';

INSERT INTO screening_settings (key, value, description) VALUES
  ('example_reserve_per_unit', '300',
   'Example replacement reserve per unit per year suggested when deferred maintenance is found. Set your own.');
