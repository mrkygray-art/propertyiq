-- Tier 2: California red-flag rules (editable), component typical-life table (R5), and module settings.
-- Rules were looked up against official sources on 2026-10-11. Rules that couldn't be confirmed from an
-- official source are stored with verified = false and show as UNVERIFIED in the app.

CREATE TABLE screening_condition_life (
    item_key      text PRIMARY KEY,
    label         text NOT NULL,
    typical_life  integer,                 -- years; NULL for "flag if present" items
    flag_if       text,                    -- e.g. a material or brand that's flagged whatever its age
    source        text NOT NULL,
    last_reviewed date NOT NULL,
    is_example    boolean NOT NULL DEFAULT true
);

CREATE TABLE screening_settings (
    key          text PRIMARY KEY,
    value        text NOT NULL,
    description  text NOT NULL
);

INSERT INTO screening_settings (key, value, description) VALUES
  ('market_stale_days', '30', 'Market data older than this many days is labeled stale.'),
  ('example_tax_rate', '0.012', 'Example property tax rate for the Prop 13 estimate. Confirm the county rate for each parcel.'),
  ('m1_price_gap_pct', '0.20', 'Flag M1 when the deal''s price per unit is this far above the sale-comp median.'),
  ('m1_cap_gap_points', '0.01', 'Flag M1 when the implied cap rate is this far below the comp median (only when comps report cap rates).');

INSERT INTO screening_condition_life (item_key, label, typical_life, flag_if, source, last_reviewed) VALUES
  ('roof_shingle',   'Roof: asphalt shingle', 22, NULL, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('roof_flat',      'Roof: flat / built-up / modified bitumen', 18, NULL, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('roof_tile',      'Roof: clay or concrete tile', 45, NULL, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('hvac',           'HVAC units', 18, NULL, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('water_heaters',  'Water heaters', 10, NULL, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('plumbing',       'Supply plumbing', 50, 'galvanized', 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('drain_lines',    'Drain lines', 60, 'cast iron', 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('electrical_panel','Electrical panels', 40, 'federal pacific|stab-lok|zinsco', 'PropertyIQ placeholder: example only, set your own (brands often flagged by inspectors; confirm with an electrician)', '2026-10-11'),
  ('aluminum_wiring','Aluminum branch wiring', NULL, 'yes', 'PropertyIQ placeholder: example only, set your own (confirm with an electrician)', '2026-10-11'),
  ('elevator',       'Elevator (modernization)', 25, NULL, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('balconies',      'Balconies and decks (inspection)', 30, NULL, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('paving',         'Parking lot and paving', 20, NULL, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('windows',        'Windows', 30, NULL, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('exterior',       'Exterior paint / stucco', 10, NULL, 'PropertyIQ placeholder: example only, set your own', '2026-10-11'),
  ('fire_life_safety','Fire / life-safety systems', 25, NULL, 'PropertyIQ placeholder: example only, set your own', '2026-10-11');

INSERT INTO screening_rules (rule_key, flag_id, jurisdiction, name, trigger, description, source_url, last_verified, verified, notes) VALUES
 ('ca_prop13_reassessment', 'R1', 'California', 'Prop 13 reassessment on purchase',
  '{"transaction": "purchase"}',
  'In California a change in ownership generally resets the taxable (assessed) value to the market value at the sale. The basic tax rate is 1% of assessed value plus voter-approved debt for the area, so a buyer''s tax bill can be much higher than the seller''s. After that, the assessed value can rise by no more than 2% a year (or inflation, if lower).',
  'https://www.boe.ca.gov/proptaxes/pdf/pub29.pdf', '2026-10-11', true,
  'The example tax rate is a placeholder. Confirm the county rate for the parcel.'),
 ('la_rso', 'R2', 'City of Los Angeles', 'Los Angeles Rent Stabilization Ordinance (RSO)',
  '{"cities": ["los angeles"], "built_on_or_before": "1978-10-01", "min_units": 2}',
  'Rental units in the City of Los Angeles whose first certificate of occupancy was issued on or before October 1, 1978 are generally covered by the RSO, which limits annual rent increases to an amount the city sets each year. A single-family home that is the only dwelling on its parcel is exempt. Check the property''s RSO status on ZIMAS (Housing tab).',
  'https://housing.lacity.gov/residents/what-is-covered-under-the-rso', '2026-10-11', true,
  'Year built is used as a stand-in for the certificate of occupancy date. Confirm on ZIMAS.'),
 ('santa_monica_rent_control', 'R2', 'City of Santa Monica', 'Santa Monica rent control',
  '{"cities": ["santa monica"], "built_before": "1979-04-10"}',
  'Most rental units in Santa Monica buildings constructed before April 10, 1979 are covered by the city''s rent control law. Most single-family homes and condominiums are exempt, and small owner-occupied buildings may qualify for an exemption.',
  'https://www.santamonica.gov/exemptions-from-rent-control', '2026-10-11', true,
  'Use the Rent Control Board''s address lookup for a specific building.'),
 ('ca_ab1482', 'R2', 'California (statewide)', 'Tenant Protection Act (AB 1482) rent cap',
  '{"older_than_years": 15, "until": "2030-01-01"}',
  'California''s statewide cap limits rent increases to 5% plus the regional CPI change, or 10%, whichever is lower, in a 12-month period. Housing with a certificate of occupancy issued within the previous 15 years is exempt (a rolling window), as are units covered by a stricter local rent control law. The law is in effect until January 1, 2030 unless extended.',
  'https://oag.ca.gov/rentcaps', '2026-10-11', true,
  'Some exemptions (for example certain single-family homes and condos) depend on facts PropertyIQ doesn''t collect.'),
 ('local_rent_rules', 'R2', 'Other California cities', 'Check local rent rules',
  '{"not_cities": ["los angeles", "santa monica"]}',
  'Many California cities, including several in the South Bay, have their own rent control or tenant protection rules. PropertyIQ doesn''t track them, so check the city''s rules for this property.',
  'https://oag.ca.gov/rentcaps', '2026-10-11', true, ''),
 ('la_soft_story', 'R3', 'City of Los Angeles', 'Los Angeles mandatory soft-story retrofit',
  '{"cities": ["los angeles"], "construction": ["wood_frame"], "built_before": "1978-01-01", "min_stories": 2, "min_units": 4, "parking": ["tuck_under", "open_ground_floor"]}',
  'The City of Los Angeles requires retrofit of wood-frame buildings of two or more stories, built under codes enacted before January 1, 1978, with ground-floor parking or similar open space ("soft story"). It doesn''t apply to residential buildings with 3 or fewer units. Owners have 2 years to show a retrofit or plans, 3.5 years to get permits, and 7 years to finish.',
  'https://dbs.lacity.gov/services/specialized-services/mandatory-retrofit-programs/soft-story-retrofit-program', '2026-10-11', true,
  'Ask for proof of retrofit or the city''s compliance status.'),
 ('la_non_ductile_concrete', 'R3', 'City of Los Angeles', 'Los Angeles non-ductile concrete retrofit',
  '{"cities": ["los angeles"], "construction": ["concrete"], "built_before": "1977-01-13"}',
  'The City of Los Angeles requires retrofit of older non-ductile concrete buildings (concrete that can fail suddenly in a strong earthquake). Trade sources report the program covers concrete buildings with a permit application before January 13, 1977; single-family homes and detached duplexes are excluded.',
  'https://www.enr.com/articles/43507-la-gives-notice-to-owners-of-older-concrete-structures-about-seismic-retrofits', NULL, false,
  'UNVERIFIED: the cutoff date comes from trade press (ENR, SGH); the LADBS page didn''t state it. Confirm with LADBS before relying on it.'),
 ('unreinforced_masonry', 'R3', 'California', 'Unreinforced masonry (URM)',
  '{"construction": ["masonry"]}',
  'Unreinforced brick or block buildings are among the most dangerous in earthquakes. California''s 1986 URM law required high-seismic cities to inventory them and adopt loss-reduction programs; many cities required retrofits.',
  'https://ssc.ca.gov/wp-content/uploads/sites/9/2020/08/cssc_2005-02_urm.pdf', NULL, false,
  'UNVERIFIED: the newest official status report found is from the mid-2000s and the state law sunset in 2009. Check the city''s current URM program.'),
 ('earthquake_zone_lookup', 'R3', 'California', 'Earthquake fault and liquefaction zones (lookup)',
  '{"always": true}',
  'Whether the parcel is in a mapped earthquake fault, liquefaction or landslide zone. PropertyIQ doesn''t query this automatically; check the state''s EQ Zapp map by address.',
  'https://www.conservation.ca.gov/cgs/geohazards/eq-zapp', '2026-10-11', true, 'Lookup not available in PropertyIQ.'),
 ('lead_paint_pre1978', 'R4', 'United States (federal)', 'Lead-based paint (built before 1978)',
  '{"built_before": "1978-01-01"}',
  'Housing built before 1978 may contain lead-based paint. Federal rules require sellers and landlords to disclose known lead hazards in pre-1978 housing.',
  'https://www.epa.gov/lead/lead-based-paint-disclosure-rule-section-1018-title-x', '2026-10-11', true, ''),
 ('asbestos_pre1981', 'R4', 'United States (federal)', 'Asbestos (built in 1980 or earlier)',
  '{"built_on_or_before_year": 1980}',
  'OSHA presumes thermal system insulation and sprayed-on or troweled-on surfacing in buildings constructed no later than 1980 contain asbestos unless testing shows otherwise. Asphalt and vinyl flooring installed by 1980 is treated the same way.',
  'https://www.osha.gov/laws-regs/regulations/standardnumber/1926/1926.1101', '2026-10-11', true, ''),
 ('environmental_lookup', 'R4', 'California', 'Nearby cleanup sites (lookup)',
  '{"always": true}',
  'Nearby contaminated or cleanup sites (leaking underground tanks, dry cleaners, industrial sites). PropertyIQ doesn''t query these automatically; search GeoTracker (Water Boards) and EnviroStor (DTSC) by address.',
  'https://geotracker.waterboards.ca.gov/', '2026-10-11', true, 'Also search https://www.envirostor.dtsc.ca.gov/public/'),
 ('fire_hazard_lookup', 'R4', 'California', 'Fire hazard severity zone (lookup)',
  '{"always": true}',
  'Whether the parcel is in a fire hazard severity zone. Check the State Fire Marshal''s map viewer by address.',
  'https://osfm.fire.ca.gov/what-we-do/community-wildfire-preparedness-and-mitigation/fire-hazard-severity-zones', '2026-10-11', true, ''),
 ('flood_lookup', 'R4', 'United States (federal)', 'Flood zone (lookup)',
  '{"always": true}',
  'Whether the parcel is in a FEMA flood zone. Check the FEMA Flood Map Service Center by address.',
  'https://msc.fema.gov/portal/home', NULL, false, 'UNVERIFIED: link not checked during the build.'),
 ('la_methane_zone', 'R4', 'City of Los Angeles', 'Los Angeles methane zones (lookup)',
  '{"cities": ["los angeles"]}',
  'Parts of Los Angeles are mapped Methane or Methane Buffer Zones, which require methane mitigation for new construction and can matter for redevelopment. Check the parcel on ZIMAS or the LADBS map.',
  'https://dbs.lacity.gov/sites/default/files/efs/forms/pc17/supplemental-plan-check-corrections-sheet-for-methane-seepage-regulations.pdf', '2026-10-11', true,
  'Lookup not available in PropertyIQ; use https://zimas.lacity.org/');
