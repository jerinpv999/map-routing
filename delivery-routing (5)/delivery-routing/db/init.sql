-- Runs once when the dr-postgis volume is first created.
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS dr_order_routes (
    id                 BIGSERIAL PRIMARY KEY,
    order_id           TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    rider_pt           GEOMETRY(Point, 4326),
    pickup_pt          GEOMETRY(Point, 4326) NOT NULL,
    drop_pt            GEOMETRY(Point, 4326) NOT NULL,
    to_pickup_m        DOUBLE PRECISION,
    to_pickup_s        DOUBLE PRECISION,
    to_drop_m          DOUBLE PRECISION NOT NULL,
    to_drop_s          DOUBLE PRECISION NOT NULL,
    to_pickup_geom     GEOMETRY(LineString, 4326),
    to_drop_geom       GEOMETRY(LineString, 4326) NOT NULL
);

CREATE INDEX IF NOT EXISTS dr_order_routes_order_idx   ON dr_order_routes (order_id);
CREATE INDEX IF NOT EXISTS dr_order_routes_created_idx ON dr_order_routes (created_at DESC);
CREATE INDEX IF NOT EXISTS dr_order_routes_pickup_gix  ON dr_order_routes USING gist (pickup_pt);

-- Live tracking tables (the API also creates these on startup, so existing volumes get them).

CREATE TABLE IF NOT EXISTS dr_live_orders (
    order_id        TEXT PRIMARY KEY,
    rider_id        TEXT,
    status          TEXT NOT NULL DEFAULT 'assigned',
    pickup_pt       GEOMETRY(Point, 4326) NOT NULL,
    drop_pt         GEOMETRY(Point, 4326) NOT NULL,
    rider_pt        GEOMETRY(Point, 4326),
    heading         DOUBLE PRECISION,
    speed_mps       DOUBLE PRECISION,
    last_seen       TIMESTAMPTZ,
    remaining_m     DOUBLE PRECISION,
    remaining_s     DOUBLE PRECISION,
    eta             TIMESTAMPTZ,
    remaining_geom  GEOMETRY(LineString, 4326),
    picked_up_at    TIMESTAMPTZ,
    delivered_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS dr_rider_locations (
    id           BIGSERIAL PRIMARY KEY,
    order_id     TEXT NOT NULL,
    rider_id     TEXT,
    recorded_at  TIMESTAMPTZ NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    pt           GEOMETRY(Point, 4326) NOT NULL,
    heading      DOUBLE PRECISION,
    speed_mps    DOUBLE PRECISION,
    accuracy_m   DOUBLE PRECISION,
    status       TEXT
);
CREATE INDEX IF NOT EXISTS dr_rider_locations_order_idx ON dr_rider_locations (order_id, recorded_at);
ALTER TABLE dr_live_orders
    ADD COLUMN IF NOT EXISTS picked_up_by       TEXT,
    ADD COLUMN IF NOT EXISTS picked_up_pt       GEOMETRY(Point, 4326),
    ADD COLUMN IF NOT EXISTS picked_up_dist_m   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS pickup_note        TEXT,
    ADD COLUMN IF NOT EXISTS delivered_by       TEXT,
    ADD COLUMN IF NOT EXISTS delivered_pt       GEOMETRY(Point, 4326),
    ADD COLUMN IF NOT EXISTS delivered_dist_m   DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS delivery_note      TEXT;
CREATE INDEX IF NOT EXISTS dr_live_orders_rider_idx ON dr_live_orders (rider_id, status);

