"""
TransitFlow — PostgreSQL / Relational Database Layer
=====================================================
This module handles all queries to PostgreSQL.

TWO ROLES ARE SERVED HERE:
  1. Relational  → dual-network transit (metro + national rail),
                   availability, fares, bookings, seat selection
  2. Vector      → policy document similarity search (pgvector)

STUDENT TASK
------------
Design your schema in databases/relational/schema.sql, seed it with
skeleton/seed_postgres.py, then implement the query functions below.

Functions prefixed with `query_`  are read-only lookups called by the agent.
Functions prefixed with `execute_` are write operations (booking/cancellation).

The vector functions (query_policy_vector_search, store_policy_document)
are already implemented — do not modify them.
"""

from __future__ import annotations

import json
import random
import string
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from skeleton.config import PG_DSN, VECTOR_TOP_K, VECTOR_SIMILARITY_THRESHOLD

# 全域 Argon2 實例
ph = PasswordHasher()


def _connect():
    """Return a new psycopg2 connection with autocommit enabled."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    return conn


def _gen_booking_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"BK-{suffix}"


def _gen_payment_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"PM-{suffix}"


# ── Example ───────────────────────────────────────────────────────────────────
# The block below shows the query pattern: open a cursor, run SQL, return rows.
# Use _connect() for read-only queries; for write operations use a manual
# connection with conn.commit() / conn.rollback() (see execute_booking below).

def example_query() -> dict:
    """Example: returns the name of the connected database."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT current_database() AS db;")
            return dict(cur.fetchone())

# TODO: Implement the query_ and execute_ functions below.
# ─────────────────────────────────────────────────────────────────────────────


# ── NATIONAL RAIL AVAILABILITY ────────────────────────────────────────────────

def query_national_rail_availability(
    origin_id: str,
    destination_id: str,
    travel_date: Optional[str] = None,
) -> list[dict]:
    """
    Return national rail schedules that serve both origin and destination stations
    in the correct order, along with seat occupancy for the requested travel date.

    Args:
        origin_id:       e.g. "NR01"
        destination_id:  e.g. "NR05"
        travel_date:     e.g. "2025-06-01" — used to count bookings; omit for general info
    """
    raise NotImplementedError("TODO: implement after designing your schema")


def query_national_rail_fare(
    schedule_id: str,
    fare_class: str,
    stops_travelled: int,
) -> Optional[dict]:
    """
    Calculate the fare for a national rail journey.

    Args:
        schedule_id:     e.g. "NR_SCH01"
        fare_class:      "standard" or "first"
        stops_travelled: number of stops between origin and destination (inclusive)

    Returns:
        dict with fare_class, base_fare_usd, per_stop_rate_usd, total_fare_usd
    """
    raise NotImplementedError("TODO: implement after designing your schema")


# ── METRO SCHEDULES & FARE ────────────────────────────────────────────────────

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """
    Return metro schedules that serve both origin and destination in the correct order.

    Args:
        origin_id:       e.g. "MS01"
        destination_id:  e.g. "MS09"
    """
    raise NotImplementedError("TODO: implement after designing your schema")


def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]:
    """
    Calculate the metro fare for a single-ticket journey.

    Args:
        schedule_id:     e.g. "MS_SCH01"
        stops_travelled: number of stops between origin and destination

    Returns:
        dict with base_fare_usd, per_stop_rate_usd, total_fare_usd
    """
    raise NotImplementedError("TODO: implement after designing your schema")


# ── SEAT SELECTION ────────────────────────────────────────────────────────────

def query_available_seats(schedule_id: str, travel_date: str, fare_class: str) -> list[dict]:
    """
    Calculates real-time seat availability for a specific train and date.
    Uses NOT EXISTS subquery to filter out seats that are currently booked.
    """
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Note: schedule_id passed by Agent is the schedule_code (Business Key, e.g., 'NR1-001')
                cur.execute("""
                    SELECT s.seat_code AS seat_id, s.coach, s.seat_row AS row, s.seat_column AS column
                    FROM national_rail_seats s
                    JOIN national_rail_schedules sch ON s.schedule_id = sch.id
                    WHERE sch.schedule_code = %s AND s.fare_class = %s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM national_rail_bookings b
                          WHERE b.schedule_id = sch.id
                            AND b.travel_date = %s::DATE
                            AND b.seat_code = s.seat_code
                            AND b.status IN ('confirmed', 'in_transit')
                      )
                    ORDER BY s.coach, s.seat_row, s.seat_column
                """, (schedule_id, fare_class, travel_date))
                
                return [dict(row) for row in cur.fetchall()]
                
    except Exception as e:
        logging.error(f"Database error in query_available_seats: {e}")
        return []


def auto_select_adjacent_seats(available_seats: list[dict], count: int) -> list[str]:
    """
    Select `count` seats that are as close together as possible (same row preferred,
    then adjacent rows). Returns a list of seat_ids.

    Args:
        available_seats: output of query_available_seats()
        count:           number of seats needed
    """
    if not available_seats or count <= 0:
        return []
    if count >= len(available_seats):
        return [s["seat_id"] for s in available_seats[:count]]

    from collections import defaultdict
    rows: dict[int, list[dict]] = defaultdict(list)
    for seat in available_seats:
        rows[seat["row"]].append(seat)

    for row_seats in sorted(rows.values(), key=lambda s: s[0]["row"]):
        if len(row_seats) >= count:
            return [s["seat_id"] for s in row_seats[:count]]

    sorted_seats = sorted(available_seats, key=lambda s: (s["row"], s["column"]))
    return [s["seat_id"] for s in sorted_seats[:count]]


# ── SEAT & USER QUERIES (Task 2b) ─────────────────────────────────────────────

def query_user_profile(user_email: str) -> Optional[dict]:
    """
    Retrieves the user's profile information.
    Enforces Soft Delete (is_active) check to ensure deactivated users are not queried.
    """
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Parameterized Query to prevent SQL Injection
                cur.execute("""
                    SELECT user_code AS user_id, user_code, full_name, first_name, surname, email, phone, date_of_birth, year_of_birth, registered_at
                    FROM users
                    WHERE email = %s AND is_active = TRUE
                """, (user_email,))
                
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logging.error(f"Database error in query_user_profile: {e}")
        return None


def query_user_bookings(user_email: str) -> dict:
    """
    Retrieves all bookings (both national rail and metro) for a specific user.
    Demonstrates advanced Relational JOINs across multiple tables.
    """
    result = {"national_rail": [], "metro": []}
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Step 1: Resolve the user's UUID securely
                cur.execute("SELECT id FROM users WHERE email = %s AND is_active = TRUE", (user_email,))
                user = cur.fetchone()
                if not user:
                    return result
                
                user_id = user["id"]

                # Step 2: Fetch National Rail Bookings with Station Names (JOIN)
                cur.execute("""
                    SELECT b.booking_ref AS booking_id, b.travel_date, b.departure_time, b.ticket_type, b.fare_class,
                           b.coach, b.seat_code, b.amount_usd, b.status,
                           o.name AS origin_station, d.name AS destination_station
                    FROM national_rail_bookings b
                    JOIN national_rail_stations o ON b.origin_station_id = o.id
                    JOIN national_rail_stations d ON b.destination_station_id = d.id
                    WHERE b.user_id = %s
                    ORDER BY b.travel_date DESC, b.departure_time DESC
                """, (user_id,))
                result["national_rail"] = [dict(r) for r in cur.fetchall()]

                # Step 3: Fetch Metro Trips with Station Names (JOIN)
                cur.execute("""
                    SELECT m.trip_ref AS trip_id, m.travel_date, m.ticket_type, m.amount_usd, m.status,
                           o.name AS origin_station, d.name AS destination_station
                    FROM metro_trips m
                    LEFT JOIN metro_stations o ON m.origin_station_id = o.id
                    LEFT JOIN metro_stations d ON m.destination_station_id = d.id
                    WHERE m.user_id = %s
                    ORDER BY m.travel_date DESC
                """, (user_id,))
                result["metro"] = [dict(r) for r in cur.fetchall()]

        return result
        
    except Exception as e:
        # Fallback to empty structure to prevent UI/Agent crashes
        logging.error(f"Database error in query_user_bookings: {e}")
        return {"national_rail": [], "metro": []}


def query_payment_info(booking_id: str) -> Optional[dict]:
    """
    Retrieves payment info for a given booking reference.
    Demonstrates handling of Polymorphic Associations (rail_booking vs metro_trip).
    """
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # We assume booking_id from the LLM Agent refers to the Business Key (e.g., 'BKG-123')
                # We check both rail bookings and metro trips.
                cur.execute("""
                    SELECT p.payment_ref AS payment_id, p.amount_usd, p.method, p.status, p.paid_at
                    FROM payments p
                    LEFT JOIN national_rail_bookings rb ON p.rail_booking_id = rb.id
                    LEFT JOIN metro_trips mt ON p.metro_trip_id = mt.id
                    WHERE rb.booking_ref = %s OR mt.trip_ref = %s
                """, (booking_id, booking_id))
                
                row = cur.fetchone()
                return dict(row) if row else None
                
    except Exception as e:
        logging.error(f"Database error in query_payment_info: {e}")
        return None


# ── TRANSACTIONAL OPERATIONS ──────────────────────────────────────────────────

def execute_booking(
    user_id: str,
    schedule_id: str,
    origin_station_id: str,
    destination_station_id: str,
    travel_date: str,
    fare_class: str,
    seat_id: str,
    ticket_type: str = "single",
) -> tuple[bool, dict | str]:
    """
    Create a national rail booking for a logged-in user.

    Args:
        user_id:                e.g. "RU01" — must match the logged-in user
        schedule_id:            e.g. "NR_SCH01"
        origin_station_id:      e.g. "NR01"
        destination_station_id: e.g. "NR05"
        travel_date:            e.g. "2025-06-01"
        fare_class:             "standard" or "first"
        seat_id:                e.g. "B05" (or "any" to auto-assign)
        ticket_type:            "single" (default) or "return"

    Returns:
        (True, booking_dict)   on success
        (False, error_message) on failure
    """
    raise NotImplementedError("TODO: implement after designing your schema")


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """
    Cancel a national rail booking owned by the given user.

    Calculates the refund amount according to the booking's service type:
      - Normal service: RF001 windows (100% / 75% / 50% / 0%)
      - Express service: RF002 windows (100% / 50% / 0%)

    Args:
        booking_id: e.g. "BK001"
        user_id:    must match the booking's user_id

    Returns:
        (True, result_dict)  with refund_amount_usd and policy note
        (False, error_msg)
    """
    raise NotImplementedError("TODO: implement after designing your schema")


# ── AUTHENTICATION QUERIES ────────────────────────────────────────────────────

def register_user(
    email: str, 
    first_name: str, 
    surname: str, 
    year_of_birth: int, 
    password: str, 
    secret_question: str, 
    secret_answer: str
) -> tuple[bool, str]:
    """
    Registers a new user in the dual-key database architecture, hashing credentials securely.

    Args:
        email (str): User's email address (must be unique).
        first_name (str): User's first name.
        surname (str): User's surname.
        year_of_birth (int): User's birth year.
        password (str): Plain text password to be hashed.
        secret_question (str): Security question for password recovery.
        secret_answer (str): Plain text answer to the security question.

    Returns:
        tuple[bool, str]: A boolean indicating success, and a message string.
    """
    try:
        # Dual-Key Architecture: Generate an external-facing Business Key (user_code)
        # while the database internally utilizes UUIDv7 for the Primary Key.
        user_code = f"USER-{uuid.uuid4().hex[:8].upper()}"
        full_name = f"{first_name} {surname}".strip()
        
        # Security Implementation: Dynamically hash passwords and secret answers using Argon2id.
        # This memory-hard algorithm embeds the salt directly into the hash string.
        pwd_hash = ph.hash(password)
        ans_hash = ph.hash(secret_answer)

        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Parameterized Query (%s) is used here to strictly prevent SQL Injection.
                # RETURNING id is used to fetch the auto-generated UUIDv7 efficiently.
                cur.execute("""
                    INSERT INTO users (user_code, full_name, first_name, surname, email, year_of_birth)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                """, (user_code, full_name, first_name, surname, email, year_of_birth))
                
                result = cur.fetchone()
                if not result:
                    return False, "Failed to create user record."
                
                user_id = result["id"]

                # Relational Design: Insert credentials mapping back to the parent user 
                # using the retrieved UUIDv7 to maintain referential integrity.
                cur.execute("""
                    INSERT INTO user_credentials (user_id, password_hash, secret_question, secret_answer_hash)
                    VALUES (%s, %s, %s, %s)
                """, (user_id, pwd_hash, secret_question, ans_hash))
                
        return True, "User registered successfully."
        
    except psycopg2.IntegrityError:
        # Graceful Error Handling: Catch UNIQUE constraint violations (e.g., duplicate emails)
        # to prevent application crashes and provide user-friendly feedback.
        return False, "Email address is already registered."
    except Exception as e:
        logging.error(f"Database error in register_user: {e}")
        return False, "An internal error occurred during registration."


def login_user(email: str, password: str) -> Optional[dict]:
    """
    Authenticates a user via email and password using Argon2id.

    Args:
        email (str): User's email address.
        password (str): Plain text password provided by the user.

    Returns:
        Optional[dict]: The user's profile dict if authentication succeeds, None otherwise.
    """
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT u.id, u.user_code, u.full_name, u.first_name, u.surname, u.email, u.is_active, c.password_hash
                    FROM users u
                    JOIN user_credentials c ON u.id = c.user_id
                    WHERE u.email = %s
                """, (email,))
                
                user = cur.fetchone()

                # Validation 1: Ensure the user record exists.
                if not user:
                    return None
                
                # Validation 2: Enforce Soft Delete Strategy. 
                # Strictly prevent deactivated/deleted users from accessing the system.
                if not user["is_active"]:
                    logging.warning(f"Login blocked for deactivated user: {email}")
                    return None

                # Security Implementation: Verify the provided password against the Argon2id hash.
                try:
                    ph.verify(user["password_hash"], password)
                    
                    # Security Precaution: Strip the sensitive hash from the dictionary 
                    # before returning it to the frontend or LLM Agent to prevent data leakage.
                    del user["password_hash"]
                    return dict(user)
                    
                except VerifyMismatchError:
                    # Authentication failed due to incorrect password.
                    return None

    except Exception as e:
        # Exception Handling: Log the raw DB error for debugging but return a safe fallback.
        logging.error(f"Database error in login_user: {e}")
        return None


def get_user_secret_question(email: str) -> Optional[str]:
    """
    Retrieves the security question for a given active user.

    Args:
        email (str): User's email address.

    Returns:
        Optional[str]: The secret question string if found, None otherwise.
    """
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Note: u.is_active = TRUE enforces the Soft Delete policy during retrieval.
                cur.execute("""
                    SELECT c.secret_question
                    FROM users u
                    JOIN user_credentials c ON u.id = c.user_id
                    WHERE u.email = %s AND u.is_active = TRUE
                """, (email,))
                
                row = cur.fetchone()
                if row:
                    return row["secret_question"]
                return None
                
    except Exception as e:
        logging.error(f"Database error in get_user_secret_question: {e}")
        return None


def verify_secret_answer(email: str, answer: str) -> bool:
    """
    Verifies the user's answer to their secret question using Argon2id.

    Args:
        email (str): User's email address.
        answer (str): Plain text answer provided by the user.

    Returns:
        bool: True if the answer is correct, False otherwise.
    """
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT c.secret_answer_hash
                    FROM users u
                    JOIN user_credentials c ON u.id = c.user_id
                    WHERE u.email = %s AND u.is_active = TRUE
                """, (email,))
                
                row = cur.fetchone()
                if not row or not row["secret_answer_hash"]:
                    return False
                
                # Use Argon2id verification for the secret answer, identical to password verification.
                try:
                    ph.verify(row["secret_answer_hash"], answer)
                    return True
                except VerifyMismatchError:
                    return False
                    
    except Exception as e:
        logging.error(f"Database error in verify_secret_answer: {e}")
        return False


def update_password(email: str, new_password: str) -> bool:
    """
    Updates a user's password securely after a successful reset verification.

    Args:
        email (str): User's email address.
        new_password (str): The new plain text password to be hashed and stored.

    Returns:
        bool: True if the password was successfully updated, False otherwise.
    """
    try:
        # Re-hash the new password using Argon2id before storing it.
        new_pwd_hash = ph.hash(new_password)
        
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Enforce Soft Delete policy: Only active users (is_active = TRUE) 
                # are permitted to perform password updates.
                cur.execute("""
                    UPDATE user_credentials c
                    SET password_hash = %s
                    FROM users u
                    WHERE c.user_id = u.id 
                      AND u.email = %s 
                      AND u.is_active = TRUE
                    RETURNING c.user_id
                """, (new_pwd_hash, email))
                
                row = cur.fetchone()
                return row is not None
                
    except Exception as e:
        logging.error(f"Database error in update_password: {e}")
        return False


# ── VECTOR / RAG QUERIES — do not modify ─────────────────────────────────────

def query_policy_vector_search(embedding: list[float], top_k: int = VECTOR_TOP_K) -> list[dict]:
    """
    Find the most relevant policy documents for a given query embedding.

    Args:
        embedding: Query vector from llm.embed(user_question)
        top_k:     Number of results to return

    Returns:
        List of dicts with title, category, content, and similarity score
    """
    sql = """
        SELECT
            title,
            category,
            content,
            1 - (embedding <=> %s::vector) AS similarity
        FROM policy_documents
        WHERE 1 - (embedding <=> %s::vector) > %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (vec_str, vec_str, VECTOR_SIMILARITY_THRESHOLD, vec_str, top_k))
            return [dict(row) for row in cur.fetchall()]


def store_policy_document(
    title: str,
    category: str,
    content: str,
    embedding: list[float],
    source_file: str = "",
) -> int:
    """
    Insert a policy document with its embedding into the database.
    Used by skeleton/seed_vectors.py — students don't need to call this directly.

    Returns:
        The new document's id
    """
    sql = """
        INSERT INTO policy_documents (title, category, content, embedding, source_file)
        VALUES (%s, %s, %s, %s::vector, %s)
        RETURNING id
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (title, category, content, vec_str, source_file))
            return cur.fetchone()[0]
