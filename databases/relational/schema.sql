-- ============================================================
--  TransitFlow PostgreSQL Schema
--  Seed data is loaded separately by: python skeleton/seed_postgres.py
--
--  TWO ROLES:
--    1. Relational - dual-network transit data
--    2. Vector     - policy documents for RAG
-- ============================================================

-- ============================================================
--  RELATIONAL SCHEMA
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;
-- Ensure UUID generation is available
CREATE EXTENSION IF NOT EXISTS "pgcrypto"; 

-- ============================================================
--  UUIDv7 Generator Function
-- ============================================================
CREATE OR REPLACE FUNCTION generate_uuid_v7()
RETURNS uuid
AS $$
DECLARE
    v_time timestamp with time zone := null;
    v_secs bigint := null;
    v_msec bigint := null;
    v_usec bigint := null;
    v_unix bigint := null;
    v_uuid bytea;
    v_pad bytea;
BEGIN
    v_time := clock_timestamp();
    v_secs := EXTRACT(EPOCH FROM v_time);
    v_msec := mod(EXTRACT(MILLISECONDS FROM v_time)::numeric, 1000::numeric)::bigint;
    v_usec := mod(EXTRACT(MICROSECONDS FROM v_time)::numeric, 1000::numeric)::bigint;
    v_unix := (v_secs * 1000) + v_msec;
    v_uuid := decode(lpad(to_hex(v_unix), 12, '0'), 'hex');
    v_pad := gen_random_bytes(10);
    v_uuid := v_uuid || v_pad;
    v_uuid := set_byte(v_uuid, 6, (b'01110000'::bit(8) | (get_byte(v_uuid, 6)::bit(8) & b'00001111'::bit(8)))::integer);
    v_uuid := set_byte(v_uuid, 8, (b'10000000'::bit(8) | (get_byte(v_uuid, 8)::bit(8) & b'00111111'::bit(8)))::integer);
    RETURN encode(v_uuid, 'hex')::uuid;
END;
$$ LANGUAGE plpgsql VOLATILE;

-- ----------------------------------------------------------------------------
-- 1. Enumerated types
-- ----------------------------------------------------------------------------
CREATE TYPE direction_enum AS ENUM ('northbound', 'southbound', 'eastbound', 'westbound');
CREATE TYPE service_type_enum AS ENUM ('normal', 'express');
CREATE TYPE ticket_type_enum AS ENUM ('single', 'return', 'day_pass');
CREATE TYPE fare_class_enum AS ENUM ('standard', 'first');
CREATE TYPE booking_status_enum AS ENUM ('confirmed', 'in_transit', 'completed', 'cancelled');
CREATE TYPE payment_method_enum AS ENUM ('credit_card', 'debit_card', 'ewallet');
CREATE TYPE payment_status_enum AS ENUM ('paid', 'refunded', 'failed');

-- ----------------------------------------------------------------------------
-- 2. Users and credentials
-- ----------------------------------------------------------------------------
CREATE TABLE users (
    -- PK Justification: Chosen UUID for external-facing entities to prevent enumeration attacks 
    -- and support future distributed microservice scaling.
    id            UUID PRIMARY KEY DEFAULT generate_uuid_v7(),
    
    -- Business Key: Retained mock data ID (e.g., RU01) as a UNIQUE constraint for system mapping.
    user_code     VARCHAR(50) UNIQUE NOT NULL,
    
    full_name     VARCHAR(200) NOT NULL,
    first_name    VARCHAR(100),
    surname       VARCHAR(100),
    email         VARCHAR(255) UNIQUE NOT NULL,
    phone         VARCHAR(30),
    date_of_birth DATE,
    year_of_birth INTEGER,
    -- Delete Strategy: Soft delete (is_active) is chosen to preserve historical bookings, 
    -- trips, and accounting records (payments) while safely marking the user as deactivated.
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE user_credentials (
    -- PK Justification: 1:1 relationship with users. UUID matches the parent table.
    user_id            UUID PRIMARY KEY,
    
    -- Security Justification: Using Argon2id. Salt is embedded directly in the hash string, 
    -- eliminating the need for a separate stored_salt column.
    password_hash      VARCHAR(255) NOT NULL,
    secret_question    VARCHAR(255),
    secret_answer_hash VARCHAR(255),
    -- FK Cascade Strategy: CASCADE is used because credentials have no independent 
    -- business value without the parent user row.
    CONSTRAINT fk_credentials_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------------
-- 3. Stations and lines
-- ----------------------------------------------------------------------------
CREATE TABLE metro_stations (
    -- PK Justification: Chosen SERIAL for infrastructure lookup tables. Data is static, 
    -- centrally managed, and Integer JOINs provide optimal B-Tree indexing performance.
    id                           SERIAL PRIMARY KEY,
    station_code                 VARCHAR(50) UNIQUE NOT NULL,
    name                         VARCHAR(100) NOT NULL,
    is_interchange_metro         BOOLEAN NOT NULL DEFAULT FALSE,
    is_interchange_national_rail BOOLEAN NOT NULL DEFAULT FALSE,
    interchange_nr_id            INTEGER -- Will be linked via FK later
);

CREATE TABLE national_rail_stations (
    id                           SERIAL PRIMARY KEY,
    station_code                 VARCHAR(50) UNIQUE NOT NULL,
    name                         VARCHAR(100) NOT NULL,
    is_interchange_national_rail BOOLEAN NOT NULL DEFAULT FALSE,
    is_interchange_metro         BOOLEAN NOT NULL DEFAULT FALSE,
    interchange_metro_id         INTEGER,
    -- FK Cascade Strategy: SET NULL allows the station to remain operational 
    -- even if its connected interchange station is permanently closed or removed.
    CONSTRAINT fk_rail_interchange_metro FOREIGN KEY (interchange_metro_id) REFERENCES metro_stations(id) ON DELETE SET NULL
);

ALTER TABLE metro_stations
    ADD CONSTRAINT fk_metro_interchange_rail
    FOREIGN KEY (interchange_nr_id) REFERENCES national_rail_stations(id) ON DELETE SET NULL;

CREATE TABLE metro_lines (
    id        SERIAL PRIMARY KEY,
    line_code VARCHAR(50) UNIQUE NOT NULL
);

CREATE TABLE metro_station_lines (
    station_id INTEGER NOT NULL,
    line_id    INTEGER NOT NULL,
    PRIMARY KEY (station_id, line_id),
    CONSTRAINT fk_msl_station FOREIGN KEY (station_id) REFERENCES metro_stations(id) ON DELETE CASCADE,
    CONSTRAINT fk_msl_line FOREIGN KEY (line_id) REFERENCES metro_lines(id) ON DELETE CASCADE
);

CREATE TABLE national_rail_lines (
    id        SERIAL PRIMARY KEY,
    line_code VARCHAR(50) UNIQUE NOT NULL
);

CREATE TABLE national_rail_station_lines (
    station_id INTEGER NOT NULL,
    line_id    INTEGER NOT NULL,
    PRIMARY KEY (station_id, line_id),
    CONSTRAINT fk_nrsl_station FOREIGN KEY (station_id) REFERENCES national_rail_stations(id) ON DELETE CASCADE,
    CONSTRAINT fk_nrsl_line FOREIGN KEY (line_id) REFERENCES national_rail_lines(id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------------
-- 4. Schedules, stops, fares, and seats
-- ----------------------------------------------------------------------------
CREATE TABLE metro_schedules (
    -- PK Justification: Chosen SERIAL for internal schedule rows; schedule_code is kept as the external business key for stable references.
    id                SERIAL PRIMARY KEY,
    schedule_code     VARCHAR(50) UNIQUE NOT NULL,
    -- FK Cascade Strategy: RESTRICT prevents accidental deletion of an active metro line 
    -- while schedules are still assigned to it.
    line_id           INTEGER NOT NULL REFERENCES metro_lines(id) ON DELETE RESTRICT,
    direction         direction_enum NOT NULL,
    base_fare_usd     NUMERIC(10,2) NOT NULL,
    per_stop_rate_usd NUMERIC(10,2) NOT NULL,
    frequency_min     INTEGER,
    operates_on       TEXT[] NOT NULL,
    CONSTRAINT chk_metro_fares_non_negative CHECK (base_fare_usd >= 0 AND per_stop_rate_usd >= 0)
);

CREATE TABLE national_rail_schedules (
    -- PK Justification: Chosen SERIAL for internal schedule rows; schedule_code is kept as the external business key for stable references.
    id            SERIAL PRIMARY KEY,
    schedule_code VARCHAR(50) UNIQUE NOT NULL,
    line_id       INTEGER NOT NULL REFERENCES national_rail_lines(id) ON DELETE RESTRICT,
    service_type  service_type_enum NOT NULL,
    direction     direction_enum NOT NULL,

    -- TASK 6 EXTENSION: store frequency-based service window from mock data
    first_train_time TIME,
    last_train_time  TIME,

    frequency_min INTEGER,
    operates_on   TEXT[] NOT NULL
);

CREATE TABLE national_rail_fares (
    -- PK Justification: Composite Key used because the natural identity of a record requires both the parent schedule and the specific element.
    schedule_id       INTEGER NOT NULL,
    fare_class        fare_class_enum NOT NULL,
    base_fare_usd     NUMERIC(10,2) NOT NULL,
    per_stop_rate_usd NUMERIC(10,2) NOT NULL,
    PRIMARY KEY (schedule_id, fare_class),
    CONSTRAINT fk_fares_schedule FOREIGN KEY (schedule_id) REFERENCES national_rail_schedules(id) ON DELETE CASCADE,
    CONSTRAINT chk_rail_fares_non_negative CHECK (base_fare_usd >= 0 AND per_stop_rate_usd >= 0)
);

CREATE TABLE metro_schedule_stops (
    -- PK Justification: Composite Key used because the natural identity of a record requires both the parent schedule and the specific element.
    schedule_id                 INTEGER NOT NULL,
    station_id                  INTEGER NOT NULL,
    stop_order                  INTEGER NOT NULL,
    travel_time_from_origin_min INTEGER NOT NULL,
    PRIMARY KEY (schedule_id, stop_order),
    UNIQUE (schedule_id, station_id),
    CONSTRAINT fk_metro_stops_schedule FOREIGN KEY (schedule_id) REFERENCES metro_schedules(id) ON DELETE CASCADE,
    CONSTRAINT fk_metro_stops_station FOREIGN KEY (station_id) REFERENCES metro_stations(id) ON DELETE RESTRICT,
    CONSTRAINT chk_metro_stop_order_positive CHECK (stop_order > 0),
    CONSTRAINT chk_metro_travel_time_non_negative CHECK (travel_time_from_origin_min >= 0)
);

CREATE TABLE national_rail_schedule_stops (
    -- PK Justification: Composite Key used because the natural identity of a record requires both the parent schedule and the specific element.
    schedule_id                 INTEGER NOT NULL,
    station_id                  INTEGER NOT NULL,
    stop_order                  INTEGER NOT NULL,
    travel_time_from_origin_min INTEGER NOT NULL,
    PRIMARY KEY (schedule_id, stop_order),
    UNIQUE (schedule_id, station_id),
    CONSTRAINT fk_rail_stops_schedule FOREIGN KEY (schedule_id) REFERENCES national_rail_schedules(id) ON DELETE CASCADE,
    CONSTRAINT fk_rail_stops_station FOREIGN KEY (station_id) REFERENCES national_rail_stations(id) ON DELETE RESTRICT,
    CONSTRAINT chk_rail_stop_order_positive CHECK (stop_order > 0),
    CONSTRAINT chk_rail_travel_time_non_negative CHECK (travel_time_from_origin_min >= 0)
);

CREATE TABLE national_rail_seats (
    schedule_id INTEGER NOT NULL,
    seat_code   VARCHAR(50) NOT NULL,
    coach       VARCHAR(10) NOT NULL,
    fare_class  fare_class_enum NOT NULL,
    seat_row    INTEGER NOT NULL,
    seat_column VARCHAR(10) NOT NULL,
    PRIMARY KEY (schedule_id, seat_code),
    CONSTRAINT fk_seats_schedule FOREIGN KEY (schedule_id) REFERENCES national_rail_schedules(id) ON DELETE CASCADE,
    CONSTRAINT chk_seat_row_positive CHECK (seat_row > 0)
);

-- ----------------------------------------------------------------------------
-- 5. Core transaction tables
-- ----------------------------------------------------------------------------
CREATE TABLE national_rail_bookings (
    -- PK Justification: Chosen UUID for high-volume transactional data.
    id                     UUID PRIMARY KEY DEFAULT generate_uuid_v7(),
    booking_ref            VARCHAR(50) UNIQUE NOT NULL,
    
    user_id                UUID NOT NULL,
    schedule_id            INTEGER NOT NULL,
    origin_station_id      INTEGER NOT NULL,
    destination_station_id INTEGER NOT NULL,
    seat_code              VARCHAR(50) NOT NULL,
    
    travel_date            DATE NOT NULL,
    departure_time         TIME NOT NULL,
    ticket_type            ticket_type_enum NOT NULL,
    fare_class             fare_class_enum NOT NULL,
    coach                  VARCHAR(10) NOT NULL,
    
    -- Design Choice (Denormalization): Cached here to avoid repeated JOINs to schedule_stops
    -- for fast availability and route history queries.
    stops_travelled        INTEGER NOT NULL,
    origin_stop_order      INTEGER NOT NULL,
    destination_stop_order INTEGER NOT NULL,
    
    -- Design Choice (Denormalization): amount_usd is a financial snapshot at the time of 
    -- booking. It must remain stable and immutable even if base fares are updated later.
    amount_usd             NUMERIC(10,2) NOT NULL,
    refund_amount_usd      NUMERIC(10,2),
    status                 booking_status_enum NOT NULL DEFAULT 'confirmed',
    booked_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    travelled_at           TIMESTAMPTZ,
    cancelled_at           TIMESTAMPTZ,

    CONSTRAINT fk_rail_bookings_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
    CONSTRAINT fk_rail_bookings_schedule FOREIGN KEY (schedule_id) REFERENCES national_rail_schedules(id) ON DELETE RESTRICT,
    CONSTRAINT fk_rail_bookings_origin FOREIGN KEY (origin_station_id) REFERENCES national_rail_stations(id) ON DELETE RESTRICT,
    CONSTRAINT fk_rail_bookings_dest FOREIGN KEY (destination_station_id) REFERENCES national_rail_stations(id) ON DELETE RESTRICT,
    CONSTRAINT fk_rail_bookings_seat FOREIGN KEY (schedule_id, seat_code) REFERENCES national_rail_seats(schedule_id, seat_code) ON DELETE RESTRICT,
    CONSTRAINT chk_booking_direction CHECK (destination_stop_order > origin_stop_order),
    CONSTRAINT chk_booking_stops_positive CHECK (stops_travelled > 0),
    CONSTRAINT chk_booking_amount_non_negative CHECK (amount_usd >= 0)
);

CREATE TABLE metro_trips (
    id                     UUID PRIMARY KEY DEFAULT generate_uuid_v7(),
    trip_ref               VARCHAR(50) UNIQUE NOT NULL,
    user_id                UUID NOT NULL,
    schedule_id            INTEGER REFERENCES metro_schedules(id) ON DELETE RESTRICT,
    origin_station_id      INTEGER REFERENCES metro_stations(id) ON DELETE RESTRICT,
    destination_station_id INTEGER REFERENCES metro_stations(id) ON DELETE RESTRICT,
    travel_date            DATE NOT NULL,
    ticket_type            ticket_type_enum NOT NULL,
    day_pass_id            UUID REFERENCES metro_trips(id) ON DELETE SET NULL,
    stops_travelled        INTEGER,
    amount_usd             NUMERIC(10,2) NOT NULL,
    status                 booking_status_enum NOT NULL DEFAULT 'in_transit',
    purchased_at           TIMESTAMPTZ,
    travelled_at           TIMESTAMPTZ,

    CONSTRAINT fk_metro_trips_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT,
    CONSTRAINT chk_metro_trip_amount_non_negative CHECK (amount_usd >= 0)
);

-- ----------------------------------------------------------------------------
-- 6. Payments and feedback
-- ----------------------------------------------------------------------------
CREATE TABLE payments (
    -- PK Justification: UUID for transactional integrity. Delete Strategy: Payments reference bookings via ON DELETE RESTRICT to ensure historical financial records are never orphaned.
    id              UUID PRIMARY KEY DEFAULT generate_uuid_v7(),
    payment_ref     VARCHAR(50) UNIQUE NOT NULL,
    rail_booking_id UUID REFERENCES national_rail_bookings(id) ON DELETE RESTRICT,
    metro_trip_id   UUID REFERENCES metro_trips(id) ON DELETE RESTRICT,
    amount_usd      NUMERIC(10,2) NOT NULL,
    method          payment_method_enum NOT NULL,
    status          payment_status_enum NOT NULL DEFAULT 'paid',
    paid_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Design Choice (Polymorphic Association): Enforces that a payment belongs to 
    -- EXACTLY ONE type of transaction (either rail or metro, but not both or neither).
    CONSTRAINT chk_payment_polymorphic CHECK (
        (rail_booking_id IS NOT NULL AND metro_trip_id IS NULL) OR
        (rail_booking_id IS NULL AND metro_trip_id IS NOT NULL)
    ),
    CONSTRAINT chk_payment_amount_non_negative CHECK (amount_usd >= 0)
);

CREATE TABLE feedback (
    id              UUID PRIMARY KEY DEFAULT generate_uuid_v7(),
    feedback_ref    VARCHAR(50) UNIQUE NOT NULL,
    user_id         UUID NOT NULL,
    rail_booking_id UUID REFERENCES national_rail_bookings(id) ON DELETE CASCADE,
    metro_trip_id   UUID REFERENCES metro_trips(id) ON DELETE CASCADE,
    rating          INTEGER NOT NULL,
    comment         TEXT,
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_feedback_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT chk_feedback_rating_range CHECK (rating >= 1 AND rating <= 5),
    CONSTRAINT chk_feedback_polymorphic CHECK (
        (rail_booking_id IS NOT NULL AND metro_trip_id IS NULL) OR
        (rail_booking_id IS NULL AND metro_trip_id IS NOT NULL)
    )
);

-- ----------------------------------------------------------------------------
-- 7. Indexes
-- ----------------------------------------------------------------------------
CREATE INDEX idx_rail_bookings_user ON national_rail_bookings(user_id);
CREATE INDEX idx_metro_trips_user ON metro_trips(user_id);
CREATE INDEX idx_payments_paid_at ON payments(paid_at);

-- ============================================================
--  VECTOR SCHEMA  (RAG / Help Desk) — do not modify
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_documents (
    id          SERIAL       PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    category    VARCHAR(50)  NOT NULL,  -- 'refund', 'booking', 'conduct'
    content     TEXT         NOT NULL,
    -- 768-dim  → Ollama nomic-embed-text (default)
    -- 3072-dim → Gemini gemini-embedding-001
    -- If you switch LLM_PROVIDER to gemini, change to vector(3072) and reset the database.
    embedding   vector(768),
    source_file VARCHAR(200),
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS policy_documents_embedding_idx
ON policy_documents
USING hnsw (embedding vector_cosine_ops);
