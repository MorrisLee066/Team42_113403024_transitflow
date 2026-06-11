"""
Seed PostgreSQL with all TransitFlow mock data from train-mock-data/.

Usage:
    python skeleton/seed_postgres.py

Run AFTER docker-compose up -d.
You must first design and create your tables in databases/relational/schema.sql.
Safe to re-run: implement your inserts with ON CONFLICT DO NOTHING.
"""

import json
import os
import sys

import psycopg2
from psycopg2.extras import execute_values
from argon2 import PasswordHasher

ph = PasswordHasher()

# ── resolve paths ────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR    = os.path.join(PROJECT_DIR, "train-mock-data")

sys.path.insert(0, PROJECT_DIR)
from skeleton import config as cfg

def load(filename):
    filepath = os.path.join(DATA_DIR, filename)
    if not os.path.exists(filepath):
        print(f"Warning: {filename} not found. Skipping.")
        return []
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)

def connect():
    return psycopg2.connect(
        host=cfg.PG_HOST,
        port=cfg.PG_PORT,
        dbname=cfg.PG_DB,
        user=cfg.PG_USER,
        password=cfg.PG_PASSWORD,
    )

# ── seeders ──────────────────────────────────────────────────────────────────

def seed_metro_stations(cur):
    """Seed metro stations and prepare for NR interchange linking."""
    data = load("metro_stations.json")
    if not data: return

    rows = []
    for item in data:
        rows.append((
            item.get("station_id"),
            item.get("name"),
            item.get("is_interchange_metro", False),
            item.get("is_interchange_national_rail", False)
        ))

    sql = """
        INSERT INTO metro_stations (
            station_code, name, is_interchange_metro, is_interchange_national_rail
        ) VALUES %s
        ON CONFLICT (station_code) DO NOTHING
    """
    execute_values(cur, sql, rows)
    print(f"Seeded metro stations: {len(rows)} rows processed.")

def seed_national_rail_stations(cur):
    """Seed NR stations and update Metro interchange links."""
    data = load("national_rail_stations.json")
    if not data: return

    rows = []
    metro_updates = []
    for item in data:
        nr_id = item.get("station_id")
        metro_id = item.get("interchange_metro_id")
        
        rows.append((
            nr_id,
            item.get("name"),
            item.get("is_interchange_national_rail", False),
            item.get("is_interchange_metro", False)
        ))
        
        if metro_id:
            metro_updates.append((nr_id, metro_id))

    sql = """
        INSERT INTO national_rail_stations (
            station_code, name, is_interchange_national_rail, is_interchange_metro
        ) VALUES %s
        ON CONFLICT (station_code) DO NOTHING
    """
    execute_values(cur, sql, rows)

    if metro_updates:
        # Update national_rail_stations with interchange_metro_id
        update_nr_sql = """
            UPDATE national_rail_stations nr
            SET interchange_metro_id = ms.id
            FROM (VALUES %s) AS data(nr_code, metro_code)
            JOIN metro_stations ms ON ms.station_code = data.metro_code
            WHERE nr.station_code = data.nr_code
        """
        execute_values(cur, update_nr_sql, metro_updates)
        
        # Update metro_stations with interchange_nr_id
        update_ms_sql = """
            UPDATE metro_stations ms
            SET interchange_nr_id = nr.id
            FROM (VALUES %s) AS data(nr_code, metro_code)
            JOIN national_rail_stations nr ON nr.station_code = data.nr_code
            WHERE ms.station_code = data.metro_code
        """
        execute_values(cur, update_ms_sql, metro_updates)
        
    print(f"Seeded national rail stations: {len(rows)} rows processed.")

def seed_metro_schedules(cur):
    """Seed metro lines, schedules, and stops (2NF junction table)."""
    data = load("metro_schedules.json")
    if not data: return

    lines = set()
    st_lines = set()
    schedules = []
    stops = []

    for item in data:
        line_id = item.get("line") or item.get("line_id")
        lines.add((line_id,))
        
        schedules.append((
            item.get("schedule_id"),
            line_id,
            item.get("direction"),
            item.get("base_fare_usd"),
            item.get("per_stop_rate_usd"),
            item.get("frequency_min"),
            item.get("operates_on", [])
        ))

        for i, st_id in enumerate(item.get("stops_in_order", [])):
            st_lines.add((st_id, line_id))
            stops.append((
                item.get("schedule_id"),
                st_id,
                i + 1,
                item.get("travel_time_from_origin_min", {}).get(st_id, 0)
            ))

    execute_values(cur, "INSERT INTO metro_lines (line_code) VALUES %s ON CONFLICT DO NOTHING", list(lines))
    
    execute_values(cur, """
        INSERT INTO metro_schedules (schedule_code, line_id, direction, base_fare_usd, per_stop_rate_usd, frequency_min, operates_on) 
        SELECT data.sch_code, ml.id, data.dir::direction_enum, data.base::numeric, data.per::numeric, data.freq::int, data.ops::text[]
        FROM (VALUES %s) AS data(sch_code, ln_code, dir, base, per, freq, ops)
        JOIN metro_lines ml ON ml.line_code = data.ln_code
        ON CONFLICT (schedule_code) DO NOTHING
    """, schedules)
    
    execute_values(cur, """
        INSERT INTO metro_schedule_stops (schedule_id, station_id, stop_order, travel_time_from_origin_min) 
        SELECT ms.id, mst.id, data.s_order::int, data.t_time::int
        FROM (VALUES %s) AS data(sch_code, st_code, s_order, t_time)
        JOIN metro_schedules ms ON ms.schedule_code = data.sch_code
        JOIN metro_stations mst ON mst.station_code = data.st_code
        ON CONFLICT (schedule_id, stop_order) DO NOTHING
    """, stops)
    
    execute_values(cur, """
        INSERT INTO metro_station_lines (station_id, line_id)
        SELECT mst.id, ml.id
        FROM (VALUES %s) AS data(st_code, ln_code)
        JOIN metro_stations mst ON mst.station_code = data.st_code
        JOIN metro_lines ml ON ml.line_code = data.ln_code
        ON CONFLICT (station_id, line_id) DO NOTHING
    """, list(st_lines))
    print(f"Seeded metro schedules: {len(schedules)} routes, {len(stops)} stops.")

# TASK 6 EXTENSION:
# Seed national_rail_schedules with first_train_time and last_train_time so
# frequency-based departure_time validation can use real service windows.
def seed_national_rail_schedules(cur):
    """Seed NR lines, schedules, fares, and stops."""
    data = load("national_rail_schedules.json")
    if not data: return

    lines = set()
    st_lines = set()
    schedules = []
    fares = []
    stops = []

    for item in data:
        line_id = item.get("line") or item.get("line_id")
        sch_id = item.get("schedule_id")
        lines.add((line_id,))
        
        schedules.append((
            sch_id, line_id, item.get("service_type"), item.get("direction"),
            item.get("first_train_time"), item.get("last_train_time"),
            item.get("frequency_min"), item.get("operates_on", [])
        ))

        fare_data = item.get("fares") or item.get("fare_classes") or {}
        for f_class, f_data in fare_data.items():
            fares.append((
                sch_id,
                f_class,
                f_data.get("base") or f_data.get("base_fare_usd"),
                f_data.get("per_stop") or f_data.get("per_stop_rate_usd")
            ))

        for i, st_id in enumerate(item.get("stops_in_order", [])):
            st_lines.add((st_id, line_id))
            stops.append((sch_id, st_id, i + 1, item.get("travel_time_from_origin_min", {}).get(st_id, 0)))

    execute_values(cur, "INSERT INTO national_rail_lines (line_code) VALUES %s ON CONFLICT DO NOTHING", list(lines))
    
    execute_values(cur, """
        INSERT INTO national_rail_schedules (
            schedule_code, line_id, service_type, direction,
            first_train_time, last_train_time, frequency_min, operates_on
        ) 
        SELECT
            data.sch_code, nl.id, data.srv::service_type_enum, data.dir::direction_enum,
            data.first_time::time, data.last_time::time, data.freq::int, data.ops::text[]
        FROM (VALUES %s) AS data(sch_code, ln_code, srv, dir, first_time, last_time, freq, ops)
        JOIN national_rail_lines nl ON nl.line_code = data.ln_code
        ON CONFLICT (schedule_code) DO NOTHING
    """, schedules)
    
    execute_values(cur, """
        INSERT INTO national_rail_fares (schedule_id, fare_class, base_fare_usd, per_stop_rate_usd) 
        SELECT ns.id, data.fc::fare_class_enum, data.base::numeric, data.per::numeric
        FROM (VALUES %s) AS data(sch_code, fc, base, per)
        JOIN national_rail_schedules ns ON ns.schedule_code = data.sch_code
        ON CONFLICT (schedule_id, fare_class) DO NOTHING
    """, fares)
    
    execute_values(cur, """
        INSERT INTO national_rail_schedule_stops (schedule_id, station_id, stop_order, travel_time_from_origin_min) 
        SELECT ns.id, nst.id, data.s_order::int, data.t_time::int
        FROM (VALUES %s) AS data(sch_code, st_code, s_order, t_time)
        JOIN national_rail_schedules ns ON ns.schedule_code = data.sch_code
        JOIN national_rail_stations nst ON nst.station_code = data.st_code
        ON CONFLICT (schedule_id, stop_order) DO NOTHING
    """, stops)
    
    execute_values(cur, """
        INSERT INTO national_rail_station_lines (station_id, line_id) 
        SELECT nst.id, nl.id
        FROM (VALUES %s) AS data(st_code, ln_code)
        JOIN national_rail_stations nst ON nst.station_code = data.st_code
        JOIN national_rail_lines nl ON nl.line_code = data.ln_code
        ON CONFLICT (station_id, line_id) DO NOTHING
    """, list(st_lines))
    print(f"Seeded NR schedules: {len(schedules)} routes, {len(stops)} stops.")

def seed_seat_layouts(cur):
    """Flatten nested JSON seat layouts into national_rail_seats table."""
    data = load("national_rail_seat_layouts.json")
    if not data: return

    rows = []
    for layout in data:
        sch_id = layout.get("schedule_id")
        for coach in layout.get("coaches", []):
            c_name = coach.get("coach")
            f_class = coach.get("fare_class")
            for seat in coach.get("seats", []):
                rows.append((
                    sch_id,
                    seat.get("seat_id"),  # e.g., "A01"
                    c_name,
                    f_class,
                    seat.get("row"),
                    seat.get("column")
                ))

    sql = """
        INSERT INTO national_rail_seats (schedule_id, seat_code, coach, fare_class, seat_row, seat_column) 
        SELECT s.id, data.seat_code, data.coach, data.fare_class::fare_class_enum, data.row::int, data.col
        FROM (VALUES %s) AS data(sch_code, seat_code, coach, fare_class, row, col)
        JOIN national_rail_schedules s ON s.schedule_code = data.sch_code
        ON CONFLICT (schedule_id, seat_code) DO NOTHING
    """
    execute_values(cur, sql, rows)
    print(f"Seeded NR seats: {len(rows)} seats flattened.")

def seed_users(cur):
    """Seed users, dynamically hash passwords and secret answers using Argon2id."""
    data = load("registered_users.json")
    if not data: return

    rows = []
    creds = []
    for user in data:
        full_name = user.get("full_name", "")
        parts = full_name.split(" ", 1)
        user_code = user.get("user_id")
        
        rows.append((
            user_code,
            full_name,
            parts[0],
            parts[1] if len(parts) > 1 else "",
            user.get("email"),
            user.get("phone"),
            user.get("date_of_birth")
        ))
        
        raw_password = user.get("password")
        raw_answer = user.get("secret_answer")
        
        hashed_password = ph.hash(raw_password) if raw_password else ""
        hashed_answer = ph.hash(raw_answer) if raw_answer else None

        creds.append((
            user_code,
            hashed_password,
            user.get("secret_question"),
            hashed_answer
        ))

    execute_values(cur, """
        INSERT INTO users (user_code, full_name, first_name, surname, email, phone, date_of_birth) 
        VALUES %s ON CONFLICT (user_code) DO NOTHING
    """, rows)
    
    execute_values(cur, """
        INSERT INTO user_credentials (user_id, password_hash, secret_question, secret_answer_hash) 
        SELECT u.id, data.hash, data.question, data.ans_hash
        FROM (VALUES %s) AS data(u_code, hash, question, ans_hash)
        JOIN users u ON u.user_code = data.u_code
        ON CONFLICT (user_id) DO NOTHING
    """, creds)
    print(f"Seeded users and hashed credentials: {len(rows)} rows processed.")

def seed_national_rail_bookings(cur):
    """Seed NR bookings."""
    data = load("bookings.json")
    if not data: return

    rows = []
    for b in data:
        rows.append((
            b.get("booking_id"), b.get("user_id"), b.get("schedule_id"),
            b.get("origin_station_id"), b.get("destination_station_id"),
            b.get("seat_id"),  # e.g., "A01" which is now seat_code
            b.get("travel_date"), b.get("departure_time"),
            b.get("ticket_type", "single"), b.get("fare_class"),
            b.get("coach"), b.get("stops_travelled"),
            b.get("amount_usd"), b.get("refund_amount_usd"),
            b.get("status", "confirmed"),
            b.get("booked_at"), b.get("travelled_at"), b.get("cancelled_at")
        ))

    sql = """
        INSERT INTO national_rail_bookings (
            booking_ref, user_id, schedule_id, origin_station_id, destination_station_id,
            seat_code, travel_date, departure_time, ticket_type, fare_class, coach,
            stops_travelled, amount_usd, refund_amount_usd, status,
            booked_at, travelled_at, cancelled_at,
            origin_stop_order, destination_stop_order
        )
        SELECT
            data.b_ref, u.id, s.id, o.id, d.id,
            data.seat_c, data.t_date::date, data.d_time::time, data.t_type::ticket_type_enum, data.f_class::fare_class_enum, data.coach,
            data.stops_t::int, data.amt::numeric, data.ref_amt::numeric, data.status::booking_status_enum,
            data.b_at::timestamptz, data.t_at::timestamptz, data.c_at::timestamptz,
            o_stop.stop_order, d_stop.stop_order
        FROM (VALUES %s) AS data(
            b_ref, u_code, s_code, o_code, d_code, seat_c,
            t_date, d_time, t_type, f_class, coach, stops_t,
            amt, ref_amt, status, b_at, t_at, c_at
        )
        JOIN users u ON u.user_code = data.u_code
        JOIN national_rail_schedules s ON s.schedule_code = data.s_code
        JOIN national_rail_stations o ON o.station_code = data.o_code
        JOIN national_rail_stations d ON d.station_code = data.d_code
        JOIN national_rail_schedule_stops o_stop 
            ON o_stop.schedule_id = s.id AND o_stop.station_id = o.id
        JOIN national_rail_schedule_stops d_stop 
            ON d_stop.schedule_id = s.id AND d_stop.station_id = d.id
        ON CONFLICT (booking_ref) DO NOTHING
    """
    execute_values(cur, sql, rows)
    print(f"Seeded NR bookings: {len(rows)} rows processed.")

def seed_metro_travels(cur):
    """Seed metro trips."""
    data = load("metro_travel_history.json")
    if not data: return

    rows = []
    for t in data:
        rows.append((
            t.get("trip_id"), t.get("user_id"), t.get("schedule_id"),
            t.get("origin_station_id"), t.get("destination_station_id"),
            t.get("travel_date"), t.get("ticket_type", "single"),
            t.get("stops_travelled"), t.get("amount_usd"),
            t.get("status", "completed"), t.get("purchased_at"), t.get("travelled_at")
        ))

    sql = """
        INSERT INTO metro_trips (
            trip_ref, user_id, schedule_id, origin_station_id, destination_station_id,
            travel_date, ticket_type, stops_travelled, amount_usd, status, purchased_at, travelled_at
        )
        SELECT
            data.t_ref, u.id, s.id, o.id, d.id,
            data.t_date::date, data.t_type::ticket_type_enum, data.stops_t::int, data.amt::numeric, 
            data.status::booking_status_enum, data.p_at::timestamptz, data.t_at::timestamptz
        FROM (VALUES %s) AS data(
            t_ref, u_code, s_code, o_code, d_code, t_date, t_type, stops_t, amt, status, p_at, t_at
        )
        JOIN users u ON u.user_code = data.u_code
        LEFT JOIN metro_schedules s ON s.schedule_code = data.s_code
        LEFT JOIN metro_stations o ON o.station_code = data.o_code
        LEFT JOIN metro_stations d ON d.station_code = data.d_code
        ON CONFLICT (trip_ref) DO NOTHING
    """
    execute_values(cur, sql, rows)
    print(f"Seeded metro travels: {len(rows)} rows processed.")

def seed_payments(cur):
    """Seed payments simply."""
    data = load("payments.json")
    if not data: return

    rows = []
    for p in data:
        b_id = p.get("booking_id")
        rows.append((
            p.get("payment_id"),
            b_id if b_id and b_id.startswith("BK") else None,
            b_id if b_id and b_id.startswith("MT") else None,
            p.get("amount_usd"),
            p.get("method"),
            p.get("status", "paid"),
            p.get("paid_at")
        ))

    sql = """
        INSERT INTO payments (
            payment_ref, rail_booking_id, metro_trip_id, amount_usd, method, status, paid_at
        )
        SELECT 
            data.p_ref, rb.id, mt.id, data.amt::numeric, data.method::payment_method_enum, data.status::payment_status_enum, data.p_at::timestamptz
        FROM (VALUES %s) AS data(p_ref, rb_ref, mt_ref, amt, method, status, p_at)
        LEFT JOIN national_rail_bookings rb ON rb.booking_ref = data.rb_ref
        LEFT JOIN metro_trips mt ON mt.trip_ref = data.mt_ref
        ON CONFLICT (payment_ref) DO NOTHING
    """
    execute_values(cur, sql, rows)
    print(f"Seeded payments: {len(rows)} rows processed.")

def seed_feedback(cur):
    """Seed feedback simply."""
    data = load("feedback.json")
    if not data: return

    rows = []
    for f in data:
        b_id = f.get("booking_id")
        rows.append((
            f.get("feedback_id"),
            f.get("user_id"),
            b_id if b_id and b_id.startswith("BK") else None,
            b_id if b_id and b_id.startswith("MT") else None,
            f.get("rating"),
            f.get("comment"),
            f.get("submitted_at")
        ))

    sql = """
        INSERT INTO feedback (
            feedback_ref, user_id, rail_booking_id, metro_trip_id, rating, comment, submitted_at
        )
        SELECT
            data.f_ref, u.id, rb.id, mt.id, data.rating::int, data.comment, data.s_at::timestamptz
        FROM (VALUES %s) AS data(f_ref, u_code, rb_ref, mt_ref, rating, comment, s_at)
        JOIN users u ON u.user_code = data.u_code
        LEFT JOIN national_rail_bookings rb ON rb.booking_ref = data.rb_ref
        LEFT JOIN metro_trips mt ON mt.trip_ref = data.mt_ref
        ON CONFLICT (feedback_ref) DO NOTHING
    """
    execute_values(cur, sql, rows)
    print(f"Seeded feedback: {len(rows)} rows processed.")

# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("Connecting to PostgreSQL...")
    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    try:
        print("Seeding tables (dependency order):")
        seed_metro_stations(cur)
        seed_national_rail_stations(cur)
        seed_metro_schedules(cur)
        seed_national_rail_schedules(cur)
        seed_seat_layouts(cur)
        seed_users(cur)
        seed_national_rail_bookings(cur)
        seed_metro_travels(cur)
        seed_payments(cur)
        seed_feedback(cur)
        conn.commit()
        print("\nAll done. Database seeded successfully.")
    except Exception as e:
        conn.rollback()
        print(f"\nError: {e}")
        raise
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()
