# AI Session Context — TransitFlow

**How to use this file:**
At the start of every AI coding session, paste the full contents of this file as your first message to your AI assistant. This gives the AI the context it needs to produce code that fits your codebase and is consistent with your teammates' work.

**Who maintains this file:**
Whoever makes a schema change or architectural decision updates this file in the same commit. Treat it like a team contract.

---

## Project Overview

TransitFlow is a Python-based AI chat assistant for a fictional transit operator. It queries three databases — PostgreSQL (relational + vector), Neo4j (graph) — and uses an LLM to answer user questions. Our task as students is to design the database schema and implement the query functions in `databases/relational/queries.py` and `databases/graph/queries.py`.

## Tech Stack

- Language: Python 3.11+
- Relational DB: PostgreSQL via `psycopg2` with `RealDictCursor`
- Graph DB: Neo4j via the `neo4j` Python driver
- Vector search: `pgvector` extension (already implemented — do not modify)
- Web UI: Gradio
- LLM: Google Gemini or local Ollama (configured via `.env`)

## Coding Conventions

- **Naming:** `snake_case` for all Python names and SQL identifiers
- **Docstrings:** All functions must have a docstring with `Args:` and `Returns:` sections
- **Return types:** Use type hints. Read-only functions return `list[dict]` or `Optional[dict]`
- **Empty results:** Return `[]` or `None` (as documented), never raise an exception for "not found"
- **SQL:** Use `%s` placeholders for all user inputs — never string-format into SQL
- **Relational pattern:** Use `_connect()` helper + `psycopg2.extras.RealDictCursor`:
  ```python
  with _connect() as conn:
      with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
          cur.execute("SELECT ...", (param,))
          return [dict(row) for row in cur.fetchall()]
  ```
- **Graph pattern:** Use `_driver()` helper + session:
  ```python
  with _driver() as driver:
      with driver.session() as session:
          result = session.run("MATCH ...", station_id=station_id)
          return [dict(record) for record in result]
  ```
- **Error Handling (Try-Catch):** All database operations and API endpoints must be wrapped in `try...except` blocks. Never allow a raw database exception to crash the application. Log the error and return a safe fallback value (e.g., `None` or `{}`).
- **Edge Cases & Math Constraints:** Handle division-by-zero explicitly. Validate inputs (e.g., check if a list is empty before accessing `[0]`).
- **Idempotency & Upserts:** All data seeding and write operations must use `ON CONFLICT DO NOTHING` or explicit `UPSERT` logic to ensure scripts can be run multiple times safely.

## Agreed Relational Schema
```sql
-- ============================================================
--  TransitFlow PostgreSQL Schema
--  Seed data is loaded separately by: python skeleton/seed_postgres.py
--
--  TWO ROLES:
--    1. Relational - dual-network transit data
--    2. Vector     - policy documents for RAG
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
    id            SERIAL PRIMARY KEY,
    schedule_code VARCHAR(50) UNIQUE NOT NULL,
    line_id       INTEGER NOT NULL REFERENCES national_rail_lines(id) ON DELETE RESTRICT,
    service_type  service_type_enum NOT NULL,
    direction     direction_enum NOT NULL,
    frequency_min INTEGER,
    operates_on   TEXT[] NOT NULL
);

CREATE TABLE national_rail_fares (
    schedule_id       INTEGER NOT NULL,
    fare_class        fare_class_enum NOT NULL,
    base_fare_usd     NUMERIC(10,2) NOT NULL,
    per_stop_rate_usd NUMERIC(10,2) NOT NULL,
    PRIMARY KEY (schedule_id, fare_class),
    CONSTRAINT fk_fares_schedule FOREIGN KEY (schedule_id) REFERENCES national_rail_schedules(id) ON DELETE CASCADE,
    CONSTRAINT chk_rail_fares_non_negative CHECK (base_fare_usd >= 0 AND per_stop_rate_usd >= 0)
);

CREATE TABLE metro_schedule_stops (
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
```

## Agreed Graph Schema

<!-- ============================================================
  FILL THIS IN after your team agrees on Neo4j node labels and
  relationship types.
  ============================================================ -->

```
Node labels:

MetroStation: 捷運車站節點。

NationalRailStation: 國鐵/台鐵車站節點。

Relationship types:

METRO_LINK: 連結相鄰的捷運車站（MetroStation 之間）。

RAIL_LINK: 連結相鄰的國鐵車站（NationalRailStation 之間）。

INTERCHANGE_WITH: 跨網路雙向轉乘連線（MetroStation 與 NationalRailStation 之間）。

Key properties:

MetroStation (Nodes): station_id, name, lines, is_interchange_nr, interchange_nr_id

NationalRailStation (Nodes): station_id, name, lines, is_interchange_m, interchange_m_id


METRO_LINK & RAIL_LINK (Relationships): line, travel_time_min, fare, fare_first 


INTERCHANGE_WITH (Relationships): travel_time_min (預設 5 分鐘), fare (預設 0.0), fare_first (預設 0.0)
```

## Function Signatures We Are Implementing

These are fixed contracts. AI-generated code must match these signatures exactly.

### Relational (`databases/relational/queries.py`)

```python
# Read-only
def query_national_rail_availability(origin_id: str, destination_id: str, travel_date: Optional[str] = None) -> list[dict]: ...
def query_national_rail_fare(schedule_id: str, fare_class: str, stops_travelled: int) -> Optional[dict]: ...
def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]: ...
def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]: ...
def query_available_seats(schedule_id: str, travel_date: str, fare_class: str) -> list[dict]: ...
def query_user_profile(user_email: str) -> Optional[dict]: ...
def query_user_bookings(user_email: str) -> dict: ...  # returns {"national_rail": [...], "metro": [...]}
def query_payment_info(booking_id: str) -> Optional[dict]: ...

# Write operations
def execute_booking(user_id, schedule_id, origin_station_id, destination_station_id, travel_date, fare_class, seat_id, ticket_type="single") -> tuple[bool, dict | str]: ...
def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]: ...

# Auth
def register_user(email, first_name, surname, year_of_birth, password, secret_question, secret_answer) -> tuple[bool, str]: ...
def login_user(email: str, password: str) -> Optional[dict]: ...
def get_user_secret_question(email: str) -> Optional[str]: ...
def verify_secret_answer(email: str, answer: str) -> bool: ...
def update_password(email: str, new_password: str) -> bool: ...
```

### Graph (`databases/graph/queries.py`)

```python
def query_shortest_route(origin_id: str, destination_id: str, network: str = "auto") -> dict: ...
def query_cheapest_route(origin_id: str, destination_id: str, network: str = "auto", fare_class: str = "standard") -> dict: ...
def query_alternative_routes(origin_id, destination_id, avoid_station_id, network="auto", max_routes=3) -> list[list[dict]]: ...
def query_interchange_path(origin_id: str, destination_id: str) -> dict: ...
def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]: ...
def query_station_connections(station_id: str) -> list[dict]: ...
```

## Team Decisions Log

<!-- Add entries as you make decisions. Format: "Decision: X. Why: Y." -->

- [x] Schema design: Refactored to a Dual-Key Architecture. Replaced VARCHAR PKs with Surrogate Keys (UUID for transactional/external entities, SERIAL for static/infrastructure entities) to improve indexing, JOIN performance, and security. Retained VARCHAR mock IDs as UNIQUE business keys for seamless data seeding. Added explicit inline comments justifying PK, Soft Delete, and FK Cascade strategies for grading compliance.
- [x] UUID Index Optimization: Implemented a custom PL/pgSQL function to generate UUIDv7 (time-ordered) instead of the default UUIDv4 (`gen_random_uuid()`). This prevents B-Tree index fragmentation and significantly improves write performance for high-volume transactional tables (`users`, `national_rail_bookings`, `metro_trips`, `payments`, `feedback`).
- [x] Execution Context: Discovered that default `agent.py` lacks `departure_time` in booking logic. Based on TA advice, we decided to ENHANCE `agent.py` and our queries to fully support timetable logic based on `frequency_min`. This will be documented as a Task 6 Bonus feature.
- [x] Security Implementation: Initially considered switching to PBKDF2 to avoid external dependencies. However, to align with the TA's specific lecture on Argon2id (v19, memory-hard hashing) and achieve maximum score in Section 2, we deliberately added `argon2-cffi` to `requirements.txt` and embedded the hashing logic securely in the seeding script.

## Prompts That Worked

<!-- Share prompts that produced good output so teammates can reuse them. -->

### Schema design prompt that worked:
```
TODO — add a prompt here after your schema design workshop
```

### Query implementation prompt that worked:
```
TODO — add after implementing your first function
```