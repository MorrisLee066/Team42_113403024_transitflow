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

## Agreed Relational Schema

<!-- ============================================================
  FILL THIS IN after your team completes the schema design workshop.
  Paste your final CREATE TABLE statements here.
  ============================================================ -->

```sql
-- TODO: paste your final schema.sql contents here after team review
-- ============================================================================
-- TransitFlow PostgreSQL Schema (v3.1 Hybrid PK Final Version)
-- Optimized for: Evaluation Rubric, Python Implementation Ease, Performance
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- ----------------------------------------------------------------------------
-- 0. 高效能密碼級 UUIDv7 產生器函數
-- (滿足老師上課對 UUIDv7 的要求，應用於高頻交易表如 bookings, payments)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION generate_uuid_v7() RETURNS UUID AS $$
DECLARE
    timestamp_ms BIGINT;
    timestamp_hex TEXT;
    random_hex TEXT;
    uuid_str TEXT;
BEGIN
    timestamp_ms := FLOOR(EXTRACT(EPOCH FROM CLOCK_TIMESTAMP()) * 1000)::BIGINT;
    timestamp_hex := LPAD(TO_HEX(timestamp_ms), 12, '0');
    random_hex := MD5(RANDOM()::TEXT || CLOCK_TIMESTAMP()::TEXT);
    uuid_str := SUBSTRING(timestamp_hex FROM 1 FOR 8) || '-' ||
                SUBSTRING(timestamp_hex FROM 9 FOR 4) || '-' ||
                '7' || SUBSTRING(random_hex FROM 13 FOR 3) || '-' ||
                CASE 
                    WHEN SUBSTRING(random_hex FROM 17 FOR 1) IN ('0','1','2','3','4','5','6','7') THEN '8'
                    WHEN SUBSTRING(random_hex FROM 17 FOR 1) IN ('8','9','a','b') THEN '9'
                    WHEN SUBSTRING(random_hex FROM 17 FOR 1) IN ('c','d','e','f') THEN 'a'
                    ELSE 'b'
                END || SUBSTRING(random_hex FROM 18 FOR 3) || '-' ||
                SUBSTRING(random_hex FROM 21 FOR 12);
    RETURN uuid_str::UUID;
END;
$$ LANGUAGE plpgsql VOLATILE;

-- ----------------------------------------------------------------------------
-- 1. 全域列舉型別 (Enums)
-- ----------------------------------------------------------------------------
CREATE TYPE direction_enum AS ENUM ('northbound', 'southbound');
CREATE TYPE service_type_enum AS ENUM ('normal', 'express');
CREATE TYPE ticket_type_enum AS ENUM ('single', 'return', 'day_pass');
CREATE TYPE fare_class_enum AS ENUM ('standard', 'first');
CREATE TYPE booking_status_enum AS ENUM ('confirmed', 'in_transit', 'completed', 'cancelled');
CREATE TYPE payment_method_enum AS ENUM ('credit_card', 'ewallet');
CREATE TYPE payment_status_enum AS ENUM ('paid', 'refunded', 'failed');

-- ----------------------------------------------------------------------------
-- 2. 使用者與安全資安模組
-- ----------------------------------------------------------------------------
CREATE TABLE users (
    user_id       VARCHAR(50) PRIMARY KEY, -- 為了 Python 寫入友善，保留 RU01
    full_name     VARCHAR(200) NOT NULL,
    first_name    VARCHAR(100),            -- 對齊 register_user 參數
    surname       VARCHAR(100),            -- 對齊 register_user 參數
    email         VARCHAR(255) UNIQUE NOT NULL,
    phone         VARCHAR(30),
    date_of_birth DATE,                    
    year_of_birth INTEGER,                 -- 對齊 register_user 參數
    is_active     BOOLEAN NOT NULL DEFAULT TRUE, -- Soft delete
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE user_credentials (
    user_id            VARCHAR(50) PRIMARY KEY,
    password_hash      VARCHAR(255) NOT NULL,
    secret_question    VARCHAR(255),
    secret_answer_hash VARCHAR(255),
    CONSTRAINT fk_credentials_user 
        FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------------
-- 3. 基礎設施模組 (車站與路線)
-- ----------------------------------------------------------------------------
CREATE TABLE metro_stations (
    station_id                   VARCHAR(50) PRIMARY KEY, -- e.g., MS01
    name                         VARCHAR(100) NOT NULL,
    is_interchange_metro         BOOLEAN NOT NULL DEFAULT FALSE,
    is_interchange_national_rail BOOLEAN NOT NULL DEFAULT FALSE,
    interchange_nr_id            VARCHAR(50)
);

CREATE TABLE national_rail_stations (
    station_id                   VARCHAR(50) PRIMARY KEY, -- e.g., NR01
    name                         VARCHAR(100) NOT NULL,
    is_interchange_national_rail BOOLEAN NOT NULL DEFAULT FALSE,
    is_interchange_metro         BOOLEAN NOT NULL DEFAULT FALSE,
    interchange_metro_id         VARCHAR(50),
    CONSTRAINT fk_rail_interchange_metro 
        FOREIGN KEY (interchange_metro_id) REFERENCES metro_stations(station_id) ON DELETE SET NULL
);

ALTER TABLE metro_stations 
    ADD CONSTRAINT fk_metro_interchange_rail 
    FOREIGN KEY (interchange_nr_id) REFERENCES national_rail_stations(station_id) ON DELETE SET NULL;

-- 路線主檔與關聯表 (Junction Tables)
CREATE TABLE metro_lines (
    line_id VARCHAR(50) PRIMARY KEY -- e.g., M1
);

CREATE TABLE metro_station_lines (
    station_id VARCHAR(50) NOT NULL,
    line_id    VARCHAR(50) NOT NULL,
    PRIMARY KEY (station_id, line_id),
    CONSTRAINT fk_msl_station FOREIGN KEY (station_id) REFERENCES metro_stations(station_id) ON DELETE CASCADE,
    CONSTRAINT fk_msl_line FOREIGN KEY (line_id) REFERENCES metro_lines(line_id) ON DELETE CASCADE
);

CREATE TABLE national_rail_lines (
    line_id VARCHAR(50) PRIMARY KEY -- e.g., NR1
);

CREATE TABLE national_rail_station_lines (
    station_id VARCHAR(50) NOT NULL,
    line_id    VARCHAR(50) NOT NULL,
    PRIMARY KEY (station_id, line_id),
    CONSTRAINT fk_nrsl_station FOREIGN KEY (station_id) REFERENCES national_rail_stations(station_id) ON DELETE CASCADE,
    CONSTRAINT fk_nrsl_line FOREIGN KEY (line_id) REFERENCES national_rail_lines(line_id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------------
-- 4. 班表、停靠站與費率模組
-- ----------------------------------------------------------------------------
CREATE TABLE metro_schedules (
    schedule_id       VARCHAR(50) PRIMARY KEY,
    line_id           VARCHAR(50) NOT NULL REFERENCES metro_lines(line_id) ON DELETE RESTRICT,
    direction         direction_enum NOT NULL,
    base_fare_usd     NUMERIC(10,2) NOT NULL,
    per_stop_rate_usd NUMERIC(10,2) NOT NULL,
    frequency_min     INTEGER,
    operates_on       TEXT[] NOT NULL
);

CREATE TABLE national_rail_schedules (
    schedule_id   VARCHAR(50) PRIMARY KEY,
    line_id       VARCHAR(50) NOT NULL REFERENCES national_rail_lines(line_id) ON DELETE RESTRICT,
    service_type  service_type_enum NOT NULL,
    direction     direction_enum NOT NULL,
    frequency_min INTEGER,
    operates_on   TEXT[] NOT NULL
);

CREATE TABLE national_rail_fares (
    schedule_id       VARCHAR(50) NOT NULL,
    fare_class        fare_class_enum NOT NULL,
    base_fare_usd     NUMERIC(10,2) NOT NULL,
    per_stop_rate_usd NUMERIC(10,2) NOT NULL,
    PRIMARY KEY (schedule_id, fare_class),
    CONSTRAINT fk_fares_schedule FOREIGN KEY (schedule_id) REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE
);

-- 停靠站接續表 (2NF 設計，滿足評分標準)
CREATE TABLE metro_schedule_stops (
    schedule_id                 VARCHAR(50) NOT NULL,
    station_id                  VARCHAR(50) NOT NULL,
    stop_order                  INTEGER NOT NULL,
    travel_time_from_origin_min INTEGER NOT NULL,
    PRIMARY KEY (schedule_id, stop_order),
    UNIQUE (schedule_id, station_id),
    CONSTRAINT fk_metro_stops_schedule FOREIGN KEY (schedule_id) REFERENCES metro_schedules(schedule_id) ON DELETE CASCADE,
    CONSTRAINT fk_metro_stops_station FOREIGN KEY (station_id) REFERENCES metro_stations(station_id) ON DELETE RESTRICT
);

CREATE TABLE national_rail_schedule_stops (
    schedule_id                 VARCHAR(50) NOT NULL,
    station_id                  VARCHAR(50) NOT NULL,
    stop_order                  INTEGER NOT NULL,
    travel_time_from_origin_min INTEGER NOT NULL,
    PRIMARY KEY (schedule_id, stop_order),
    UNIQUE (schedule_id, station_id),
    CONSTRAINT fk_rail_stops_schedule FOREIGN KEY (schedule_id) REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE,
    CONSTRAINT fk_rail_stops_station FOREIGN KEY (station_id) REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT
);

-- 座位表 (攤平設計 反正規化)
CREATE TABLE national_rail_seats (
    id          UUID PRIMARY KEY DEFAULT generate_uuid_v7(),
    schedule_id VARCHAR(50) NOT NULL,
    seat_code   VARCHAR(50) NOT NULL,
    coach       VARCHAR(10) NOT NULL,
    fare_class  fare_class_enum NOT NULL,
    seat_row    INTEGER NOT NULL,
    seat_column VARCHAR(10) NOT NULL,
    UNIQUE (schedule_id, seat_code),
    CONSTRAINT fk_seats_schedule FOREIGN KEY (schedule_id) REFERENCES national_rail_schedules(schedule_id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------------
-- 5. 核心交易模組 (採用 UUIDv7 提升高併發寫入效能)
-- ----------------------------------------------------------------------------
CREATE TABLE national_rail_bookings (
    id                     UUID PRIMARY KEY DEFAULT generate_uuid_v7(),
    booking_ref            VARCHAR(50) UNIQUE NOT NULL, -- e.g., BK001
    
    user_id                VARCHAR(50) NOT NULL,
    schedule_id            VARCHAR(50) NOT NULL,
    origin_station_id      VARCHAR(50) NOT NULL,
    destination_station_id VARCHAR(50) NOT NULL,
    seat_id                UUID NOT NULL,
    
    travel_date            DATE NOT NULL,
    departure_time         TIME NOT NULL,
    ticket_type            ticket_type_enum NOT NULL,
    fare_class             fare_class_enum NOT NULL,
    coach                  VARCHAR(10) NOT NULL,
    
    stops_travelled        INTEGER NOT NULL,
    origin_stop_order      INTEGER NOT NULL, -- 區間查詢反正規化
    destination_stop_order INTEGER NOT NULL,
    
    amount_usd             NUMERIC(10,2) NOT NULL,
    refund_amount_usd      NUMERIC(10,2),
    status                 booking_status_enum NOT NULL DEFAULT 'confirmed',
    
    booked_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    travelled_at           TIMESTAMPTZ,
    cancelled_at           TIMESTAMPTZ,
    
    CONSTRAINT fk_rail_bookings_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE RESTRICT,
    CONSTRAINT fk_rail_bookings_schedule FOREIGN KEY (schedule_id) REFERENCES national_rail_schedules(schedule_id) ON DELETE RESTRICT,
    CONSTRAINT fk_rail_bookings_origin FOREIGN KEY (origin_station_id) REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    CONSTRAINT fk_rail_bookings_dest FOREIGN KEY (destination_station_id) REFERENCES national_rail_stations(station_id) ON DELETE RESTRICT,
    CONSTRAINT fk_rail_bookings_seat FOREIGN KEY (seat_id) REFERENCES national_rail_seats(id) ON DELETE RESTRICT,
    CONSTRAINT chk_booking_direction CHECK (destination_stop_order > origin_stop_order)
);

CREATE TABLE metro_trips (
    id                     UUID PRIMARY KEY DEFAULT generate_uuid_v7(),
    trip_ref               VARCHAR(50) UNIQUE NOT NULL,
    user_id                VARCHAR(50) NOT NULL,
    schedule_id            VARCHAR(50) REFERENCES metro_schedules(schedule_id) ON DELETE RESTRICT,
    origin_station_id      VARCHAR(50) REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    destination_station_id VARCHAR(50) REFERENCES metro_stations(station_id) ON DELETE RESTRICT,
    travel_date            DATE NOT NULL,
    ticket_type            ticket_type_enum NOT NULL,
    day_pass_ref           UUID REFERENCES metro_trips(id) ON DELETE SET NULL,
    stops_travelled        INTEGER,
    amount_usd             NUMERIC(10,2) NOT NULL,
    status                 booking_status_enum NOT NULL DEFAULT 'in_transit',
    purchased_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    travelled_at           TIMESTAMPTZ,
    CONSTRAINT fk_metro_trips_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE RESTRICT
);

-- 多型關聯付款表
CREATE TABLE payments (
    id              UUID PRIMARY KEY DEFAULT generate_uuid_v7(),
    payment_ref     VARCHAR(50) UNIQUE NOT NULL,
    rail_booking_id UUID REFERENCES national_rail_bookings(id) ON DELETE RESTRICT,
    metro_trip_id   UUID REFERENCES metro_trips(id) ON DELETE RESTRICT,
    amount_usd      NUMERIC(10,2) NOT NULL,
    method          payment_method_enum NOT NULL,
    status          payment_status_enum NOT NULL DEFAULT 'paid',
    paid_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_payment_polymorphic CHECK (
        (rail_booking_id IS NOT NULL AND metro_trip_id IS NULL) OR
        (rail_booking_id IS NULL AND metro_trip_id IS NOT NULL)
    )
);

CREATE TABLE feedback (
    id              UUID PRIMARY KEY DEFAULT generate_uuid_v7(),
    feedback_ref    VARCHAR(50) UNIQUE NOT NULL,
    user_id         VARCHAR(50) NOT NULL,
    rail_booking_id UUID REFERENCES national_rail_bookings(id) ON DELETE CASCADE,
    metro_trip_id   UUID REFERENCES metro_trips(id) ON DELETE CASCADE,
    rating          INTEGER NOT NULL,
    comment         TEXT,
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_feedback_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT chk_feedback_rating_range CHECK (rating >= 1 AND rating <= 5),
    CONSTRAINT chk_feedback_polymorphic CHECK (
        (rail_booking_id IS NOT NULL AND metro_trip_id IS NULL) OR
        (rail_booking_id IS NULL AND metro_trip_id IS NOT NULL)
    )
);

-- 索引優化
CREATE INDEX idx_fk_rail_bookings_user ON national_rail_bookings(user_id);
CREATE INDEX idx_fk_metro_trips_user ON metro_trips(user_id);
CREATE INDEX idx_fk_payments_paid_at ON payments(paid_at);

-- ============================================================
-- VECTOR SCHEMA (Do not modify)
-- ============================================================
CREATE TABLE IF NOT EXISTS policy_documents (
    id          SERIAL       PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    category    VARCHAR(50)  NOT NULL, 
    content     TEXT         NOT NULL,
    embedding   vector(768),
    source_file VARCHAR(200),
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ON policy_documents USING hnsw (embedding vector_cosine_ops);
```

## Agreed Graph Schema

<!-- ============================================================
  FILL THIS IN after your team agrees on Neo4j node labels and
  relationship types.
  ============================================================ -->

```
哈囉！我是「資料庫」。根據你上傳的 `seed_neo4j.py` 實作細節以及我們先前對於「圖形與關聯分離」與修正權重陷阱的討論，我已經幫你把 `AI_SESSION_CONTEXT.md` 裡面遺漏的 Graph Schema 規劃整理好了。

你可以直接將以下內容複製，並完全取代掉文件中的 TODO 區塊：

---

**Node labels:**

* `MetroStation`: 捷運車站節點。
* `NationalRailStation`: 國鐵/台鐵車站節點。

**Relationship types:**

* `METRO_LINK`: 連結相鄰的捷運車站（MetroStation 之間）。
* `RAIL_LINK`: 連結相鄰的國鐵車站（NationalRailStation 之間）。
* `INTERCHANGE_WITH`: 跨網路雙向轉乘連線（MetroStation 與 NationalRailStation 之間）。

**Key properties:**

* `MetroStation` (Nodes): `station_id`, `name`, `lines`, `is_interchange_nr`, `interchange_nr_id`
* `NationalRailStation` (Nodes): `station_id`, `name`, `lines`, `is_interchange_m`, `interchange_m_id`
* 
`METRO_LINK` & `RAIL_LINK` (Relationships): `line`, `travel_time_min`, `fare`, `fare_first` 


* 
`INTERCHANGE_WITH` (Relationships): `travel_time_min` (預設 5 分鐘), `fare` (預設 0.0), `fare_first` (預設 0.0)
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

- [ ] Schema design: TODO — add your table/column decisions here
- [ ] Graph schema: TODO — add your node label and relationship type decisions here
- [ ] (example) Metro schedule stop ordering: using `jsonb_array_elements` approach — easier to debug than containment operators

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
