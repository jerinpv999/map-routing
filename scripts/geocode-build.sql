-- Turns the raw osm2pgsql import (schema dr_geo_import) into the address search index,
-- then swaps it in as schema dr_geo in one transaction (search keeps working while building).
\set ON_ERROR_STOP on
SET client_min_messages = warning;

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

-- lower case, no accents, punctuation -> space, single spaces
CREATE OR REPLACE FUNCTION public.dr_norm(t text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT trim(regexp_replace(regexp_replace(lower(public.unaccent('public.unaccent'::regdictionary, coalesce(t, ''))),
                                            '[^[:alnum:][:space:]]+', ' ', 'g'), '\s+', ' ', 'g'))
$$;

SET search_path = dr_geo_import, public;

CREATE INDEX ON places USING gist (geom);
CREATE INDEX ON streets USING gist (geom);
-- big boundary polygons are split into small pieces so point-in-polygon tests stay fast
CREATE TABLE admin_sub AS
  SELECT name, admin_level, ST_Subdivide(geom, 200) AS geom FROM admin;
CREATE INDEX ON admin_sub USING gist (geom);
ANALYZE places; ANALYZE admin_sub;

-- ---------------------------------------------------------------- helpers --
-- nearest small place (locality) within 3 km; city within 20 km, else town within 10 km;
-- district (admin 5, else 6) and state (admin 4) containing the point.
-- Nearest-neighbour (<->) uses the spatial index; the distance check comes after.
CREATE OR REPLACE FUNCTION ctx(p geometry, OUT locality text, OUT city text, OUT district text, OUT state text)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
  SELECT
    (SELECT x.name FROM (SELECT pl.name, pl.geom FROM places pl
        WHERE pl.place IN ('suburb','neighbourhood','quarter','village','hamlet','locality','borough','isolated_dwelling')
        ORDER BY pl.geom <-> p LIMIT 1) x
      WHERE ST_DWithin(x.geom::geography, p::geography, 3000)),
    coalesce(
      (SELECT x.name FROM (SELECT pl.name, pl.geom FROM places pl WHERE pl.place = 'city'
          ORDER BY pl.geom <-> p LIMIT 1) x
        WHERE ST_DWithin(x.geom::geography, p::geography, 20000)),
      (SELECT x.name FROM (SELECT pl.name, pl.geom FROM places pl WHERE pl.place = 'town'
          ORDER BY pl.geom <-> p LIMIT 1) x
        WHERE ST_DWithin(x.geom::geography, p::geography, 10000))),
    (SELECT a.name FROM admin_sub a WHERE a.admin_level IN (5, 6) AND ST_Intersects(a.geom, p)
      ORDER BY a.admin_level LIMIT 1),
    (SELECT a.name FROM admin_sub a WHERE a.admin_level = 4 AND ST_Intersects(a.geom, p) LIMIT 1)
$$;

CREATE TABLE features (
  id          bigserial PRIMARY KEY,
  kind        text NOT NULL,          -- place | street | poi | address
  category    text,                   -- place type, highway type or e.g. amenity=hospital
  name        text NOT NULL,
  names       text,
  housenumber text,
  housename   text,
  road        text,
  locality    text,
  city        text,
  district    text,
  state       text,
  postcode    text,
  importance  real NOT NULL DEFAULT 0,
  geom        geometry(Point, 4326) NOT NULL,
  search      text
);

-- ------------------------------------------------------------------ places --
INSERT INTO features (kind, category, name, names, locality, city, district, state, postcode, importance, geom)
SELECT 'place', p.place, p.name, p.names,
       CASE WHEN p.place IN ('city','town') THEN NULL ELSE c.locality END,
       CASE WHEN p.place IN ('city','town') THEN NULL ELSE c.city END,
       c.district, c.state, p.postcode,
       CASE p.place WHEN 'city' THEN 30 WHEN 'town' THEN 24 WHEN 'suburb' THEN 18 WHEN 'borough' THEN 18
                    WHEN 'village' THEN 16 WHEN 'quarter' THEN 14 WHEN 'neighbourhood' THEN 13
                    WHEN 'locality' THEN 9 ELSE 8 END
         + least(coalesce(nullif(regexp_replace(p.population, '[^0-9]', '', 'g'), '')::numeric, 0) / 200000, 6),
       p.geom
FROM places p CROSS JOIN LATERAL ctx(p.geom) c;
-- a place's own name is not its locality
UPDATE features SET locality = NULL WHERE kind = 'place' AND locality = name;

-- ----------------------------------------------------------------- streets --
-- OSM splits a road into many segments. Group segments of the same name per ~2 km
-- grid cell first, look up the area once per group, then merge groups that share
-- a name and locality/town into one search entry.
CREATE TEMP TABLE street_groups AS
SELECT dr_norm(min(s.name)) AS key, min(s.name) AS name, string_agg(DISTINCT s.names, ' ; ') AS names,
       min(s.highway) AS highway,
       max(CASE s.highway WHEN 'motorway' THEN 12 WHEN 'trunk' THEN 11 WHEN 'primary' THEN 10 WHEN 'secondary' THEN 9
                          WHEN 'tertiary' THEN 8 WHEN 'residential' THEN 6 WHEN 'service' THEN 3 ELSE 5 END) AS importance,
       ST_ClosestPoint(ST_Collect(s.geom), ST_Centroid(ST_Collect(s.geom))) AS geom
FROM streets s
WHERE ST_NPoints(s.geom) >= 2
GROUP BY dr_norm(s.name), ST_SnapToGrid(ST_LineInterpolatePoint(s.geom, 0.5), 0.02);

CREATE TEMP TABLE street_ctx AS
SELECT g.*, c.locality, c.city, c.district, c.state FROM street_groups g CROSS JOIN LATERAL ctx(g.geom) c;

INSERT INTO features (kind, category, name, names, road, locality, city, district, state, importance, geom)
SELECT 'street', min(highway), min(name), string_agg(DISTINCT names, ' ; '), min(name),
       locality, city, district, state, max(importance),
       ST_ClosestPoint(ST_Collect(geom), ST_Centroid(ST_Collect(geom)))
FROM street_ctx
GROUP BY key, locality, city, district, state;

-- ------------------------------------------------------------------- POIs --
INSERT INTO features (kind, category, name, names, housenumber, road, locality, city, district, state, postcode, importance, geom)
SELECT 'poi', p.category, p.name, p.names, p.housenumber, p.street,
       c.locality, coalesce(c.city, p.city), c.district, c.state, p.postcode,
       CASE WHEN p.category IN ('shop=mall','amenity=hospital','railway=station','aeroway=aerodrome','amenity=university',
                                'amenity=bus_station','tourism=attraction','amenity=college','public_transport=station')
            THEN 14
            WHEN p.category LIKE 'building=%' OR p.category LIKE 'landuse=%' THEN 7
            ELSE 10 END,
       p.geom
FROM pois p CROSS JOIN LATERAL ctx(p.geom) c;

-- -------------------------------------------------------------- addresses --
-- name shown in suggestions: "Rose Villa 12/345, Lulu Road", "12/345, Lulu Road", "Rose Villa", or addr:full
INSERT INTO features (kind, category, name, names, housenumber, housename, road, locality, city, district, state,
                      postcode, importance, geom)
SELECT 'address', 'house',
       coalesce(nullif(concat_ws(', ', nullif(concat_ws(' ', a.housename, a.housenumber), ''),
                                       coalesce(a.street, a.place)), ''), a.addr_full),
       concat_ws(' ; ', a.housename, a.addr_full),
       a.housenumber, a.housename, coalesce(a.street, a.place),
       c.locality, coalesce(c.city, a.city), c.district, c.state, a.postcode,
       CASE WHEN a.housename IS NOT NULL THEN 5 ELSE 4 END, a.geom
FROM addresses a CROSS JOIN LATERAL ctx(a.geom) c
WHERE coalesce(a.housenumber, a.housename, a.addr_full) IS NOT NULL;

-- --------------------------------------------------------- search text ----
-- includes the kind of place for POIs ("hospital", "mall", "bus station") so "amrita hospital" matches
UPDATE features SET search = dr_norm(concat_ws(' ', names, name, housenumber, road,
    CASE WHEN kind = 'poi' THEN replace(split_part(category, '=', 2), '_', ' ') END,
    locality, city, district, postcode));
UPDATE features SET search = dr_norm(name) WHERE search IS NULL OR search = '';

CREATE INDEX features_search_trgm ON features USING gin (search gin_trgm_ops);
CREATE INDEX features_geom ON features USING gist (geom);
CREATE INDEX features_kind ON features (kind);
CREATE INDEX features_geog ON features USING gist ((geom::geography));

-- keep street lines for "which road is this pin on"
CREATE TABLE street_lines AS SELECT name, highway, geom FROM streets;
CREATE INDEX street_lines_geom ON street_lines USING gist (geom);
CREATE INDEX street_lines_geog ON street_lines USING gist ((geom::geography));

-- raw import tables are no longer needed
DROP TABLE pois, addresses, streets;
DROP TABLE admin;            -- admin_sub keeps the same data in small pieces
ANALYZE features; ANALYZE street_lines; ANALYZE places; ANALYZE admin_sub;

-- -------------------------------------------------------------- swap in ---
BEGIN;
DROP SCHEMA IF EXISTS dr_geo CASCADE;
ALTER SCHEMA dr_geo_import RENAME TO dr_geo;
-- ctx() refers to its tables by search_path; pin it to the new schema
ALTER FUNCTION dr_geo.ctx(geometry) SET search_path = dr_geo, public;
COMMIT;

SELECT kind, count(*) FROM dr_geo.features GROUP BY kind ORDER BY kind;
