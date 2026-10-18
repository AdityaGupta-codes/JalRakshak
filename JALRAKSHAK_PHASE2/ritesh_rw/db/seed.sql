-- Minimal seed for development. Daily consumption history, forecasts and alerts are added by the
-- ML pipeline (ml/), which docker compose runs automatically after the database starts.

INSERT INTO zones (name, population) VALUES
  ('North Zone', 40000), ('Central Zone', 65000), ('South Zone', 30000);

INSERT INTO users (name, user_type, priority, zone_id) VALUES
  ('City Hospital',      'hospital',   5, 2),
  ('Green Valley School','school',     4, 1),
  ('Rajpur Colony',      'domestic',   4, 1),
  ('Shanti Apartments',  'domestic',   4, 2),
  ('Metro Steel Works',  'industry',   2, 3),
  ('Textile Mill',       'industry',   2, 3),
  ('Central Mall',       'commercial', 2, 2),
  ('Town Park',          'park',       1, 2);

INSERT INTO reservoirs (name, capacity_litres, available_litres) VALUES
  ('Main Reservoir', 200000, 50000);

-- 8 requests totalling 80,000 L against 50,000 L available (the scarcity example).
-- Defined once in schema.sql so "Reset demo data" restores exactly the same requests.
SELECT load_sample_requests(1);
