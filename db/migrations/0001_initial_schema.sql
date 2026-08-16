-- 0001_initial_schema.sql
-- Initial schema: listings, transactions, price_change_events

CREATE TABLE listings (
    id              text PRIMARY KEY,
    url             text NOT NULL,
    property_type   text,
    rooms           integer,
    surface_m2      numeric,
    chambres        integer,
    dpe             text,
    terrain_m2      numeric,
    has_garage      boolean NOT NULL DEFAULT false,
    has_ascenseur   boolean NOT NULL DEFAULT false,
    has_balcon      boolean NOT NULL DEFAULT false,
    price           integer NOT NULL,
    has_asterisk    boolean NOT NULL DEFAULT false,
    location        text,
    amenities       text[],
    description     text,
    first_seen_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE transactions (
    id_mutation             bigint PRIMARY KEY,
    date                    date NOT NULL,
    year                    smallint NOT NULL,
    price                   numeric NOT NULL,
    is_vefa                 boolean NOT NULL,
    nature_mutation_code    smallint NOT NULL,
    nature_mutation_label   text NOT NULL,
    property_type_code      text NOT NULL,
    property_type_label     text NOT NULL,
    surface_bati            numeric NOT NULL,
    surface_terrain         numeric NOT NULL,
    rooms                   integer,
    commune_code            text NOT NULL
);

CREATE INDEX idx_transactions_commune_date ON transactions (commune_code, date);

CREATE TABLE price_change_events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    listing_id  text NOT NULL REFERENCES listings(id) ON DELETE RESTRICT,
    detected_at timestamptz NOT NULL DEFAULT now(),
    old_price   integer NOT NULL,
    new_price   integer NOT NULL,
    delta_eur   integer NOT NULL,
    delta_pct   numeric NOT NULL
);

CREATE INDEX idx_price_change_events_listing_id ON price_change_events (listing_id);