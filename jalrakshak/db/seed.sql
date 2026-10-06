-- Minimal seed for development. Bulk consumption data comes from ml/generate_data.py

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

-- 8 requests totalling 80,000 L against 50,000 L available (the scarcity example)
INSERT INTO water_requests
  (user_id, reservoir_id, requested_litres, urgency, arrival_time, deadline) VALUES
  (5, 1, 20000, 2, now(),                       now() + interval '8 hours'),
  (3, 1, 12000, 4, now() + interval '5 minutes', now() + interval '3 hours'),
  (1, 1, 10000, 5, now() + interval '10 minutes', now() + interval '1 hour'),
  (7, 1,  8000, 2, now() + interval '12 minutes', now() + interval '6 hours'),
  (2, 1,  6000, 3, now() + interval '15 minutes', now() + interval '4 hours'),
  (8, 1,  4000, 1, now() + interval '20 minutes', now() + interval '10 hours'),
  (4, 1, 15000, 4, now() + interval '25 minutes', now() + interval '3 hours'),
  (6, 1,  5000, 2, now() + interval '30 minutes', now() + interval '8 hours');
