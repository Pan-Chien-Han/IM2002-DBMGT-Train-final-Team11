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
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

from skeleton.config import PG_DSN, VECTOR_TOP_K, VECTOR_SIMILARITY_THRESHOLD

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
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


# ── NATIONAL RAIL AVAILABILITY ────────────────────────────────────────────────

def query_national_rail_availability(
    origin_id: str,
    destination_id: str,
    travel_date: Optional[str] = None,
) -> list[dict]:

    sql = """
        SELECT *
        FROM national_rail_schedules
    """

    results = []

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            cur.execute(sql)

            schedules = cur.fetchall()

            for schedule in schedules:

                stops = schedule["stops_in_order"]

                if origin_id in stops and destination_id in stops:

                    origin_index = stops.index(origin_id)
                    destination_index = stops.index(destination_id)

                    # ensure correct direction
                    if origin_index < destination_index:

                        occupancy = 0

                        # count bookings if travel_date provided
                        if travel_date:

                            cur.execute(
                                """
                                SELECT COUNT(*)
                                FROM national_rail_bookings
                                WHERE schedule_id = %s
                                AND travel_date = %s
                                AND status = 'confirmed'
                                """,
                                (
                                    schedule["schedule_id"],
                                    travel_date
                                )
                            )

                            occupancy = cur.fetchone()["count"]

                        results.append({
                            "schedule_id": schedule["schedule_id"],
                            "line": schedule["line"],
                            "service_type": schedule["service_type"],
                            "direction": schedule["direction"],
                            "origin_station_id": schedule["origin_station_id"],
                            "destination_station_id": schedule["destination_station_id"],
                            "first_train_time": str(schedule["first_train_time"]),
                            "last_train_time": str(schedule["last_train_time"]),
                            "frequency_min": schedule["frequency_min"],
                            "current_bookings": occupancy
                        })

    return results

def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]:
    """
    Calculate the metro fare for a single-ticket journey.
    """

    sql = """
        SELECT
            base_fare_usd,
            per_stop_rate_usd
        FROM metro_schedules
        WHERE schedule_id = %s
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            cur.execute(sql, (schedule_id,))
            row = cur.fetchone()

            if not row:
                return None

            base_fare = float(row["base_fare_usd"])
            per_stop = float(row["per_stop_rate_usd"])

            total = base_fare + (per_stop * stops_travelled)

            return {
                "base_fare_usd": base_fare,
                "per_stop_rate_usd": per_stop,
                "stops_travelled": stops_travelled,
                "total_fare_usd": round(total, 2)
            }
        
# ── METRO SCHEDULES & FARE ────────────────────────────────────────────────────

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """
    Return metro schedules that serve both origin and destination in the correct order.
    """

    sql = """
        SELECT *
        FROM metro_schedules
    """

    results = []

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            cur.execute(sql)

            schedules = cur.fetchall()

            for schedule in schedules:

                stops = schedule["stops_in_order"]

                if origin_id in stops and destination_id in stops:

                    origin_index = stops.index(origin_id)
                    destination_index = stops.index(destination_id)

                    # ensure correct direction
                    if origin_index < destination_index:

                        results.append({
                            "schedule_id": schedule["schedule_id"],
                            "line": schedule["line"],
                            "direction": schedule["direction"],
                            "origin_station_id": schedule["origin_station_id"],
                            "destination_station_id": schedule["destination_station_id"],
                            "first_train_time": str(schedule["first_train_time"]),
                            "last_train_time": str(schedule["last_train_time"]),
                            "frequency_min": schedule["frequency_min"],
                        })

    return results


def query_national_rail_fare(
    schedule_id: str,
    fare_class: str,
    stops_travelled: int,
) -> Optional[dict]:
    """
    Calculate the fare for a national rail journey.
    """

    sql = """
        SELECT fare_classes
        FROM national_rail_schedules
        WHERE schedule_id = %s
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            cur.execute(sql, (schedule_id,))
            row = cur.fetchone()

            if not row:
                return None

            fare_classes = row["fare_classes"]

            if fare_class not in fare_classes:
                return None

            fare_data = fare_classes[fare_class]

            base_fare = float(fare_data["base_fare_usd"])
            per_stop = float(fare_data["per_stop_rate_usd"])

            total = base_fare + (per_stop * stops_travelled)

            return {
                "fare_class": fare_class,
                "base_fare_usd": base_fare,
                "per_stop_rate_usd": per_stop,
                "stops_travelled": stops_travelled,
                "total_fare_usd": round(total, 2)
            }

# ── SEAT SELECTION ────────────────────────────────────────────────────────────

def query_available_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str,
) -> list[dict]:

    sql = """
        SELECT coaches
        FROM national_rail_seat_layouts
        WHERE schedule_id = %s
    """

    available = []

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            cur.execute(sql, (schedule_id,))
            row = cur.fetchone()

            if not row:
                return []

            coaches = row["coaches"]

            cur.execute(
                """
                SELECT seat_id
                FROM national_rail_bookings
                WHERE schedule_id = %s
                AND travel_date = %s
                AND fare_class = %s
                AND status IN ('confirmed', 'completed')
                """,
                (schedule_id, travel_date, fare_class)
            )

            booked_seats = {r["seat_id"] for r in cur.fetchall()}

            for coach in coaches:
                if coach["fare_class"] != fare_class:
                    continue

                for seat in coach["seats"]:
                    if seat["seat_id"] not in booked_seats:
                        available.append({
                            "seat_id": seat["seat_id"],
                            "coach": coach["coach"],
                            "row": seat["row"],
                            "column": seat["column"],
                        })

    return available


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


# ── USER & BOOKING QUERIES ────────────────────────────────────────────────────

def query_user_profile(user_email: str) -> Optional[dict]:
    """Return a user's profile by email."""

    sql = """
        SELECT
            user_id,
            full_name,
            email,
            phone,
            date_of_birth,
            secret_question,
            registered_at,
            is_active
        FROM registered_users
        WHERE email = %s
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            cur.execute(sql, (user_email,))
            result = cur.fetchone()

            if result:
                return dict(result)

            return None

def query_user_bookings(user_email: str) -> dict:
    """
    Return a user's combined booking history (national rail + metro).
    """

    result = {
        "national_rail": [],
        "metro": []
    }

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            # find user
            cur.execute(
                """
                SELECT user_id
                FROM registered_users
                WHERE email = %s
                """,
                (user_email,)
            )

            user = cur.fetchone()

            if not user:
                return result

            user_id = user["user_id"]

            # national rail bookings
            cur.execute(
                """
                SELECT
                    booking_id,
                    schedule_id,
                    origin_station_id,
                    destination_station_id,
                    travel_date,
                    departure_time,
                    fare_class,
                    coach,
                    seat_id,
                    amount_usd,
                    status
                FROM national_rail_bookings
                WHERE user_id = %s
                ORDER BY booked_at DESC
                """,
                (user_id,)
            )

            result["national_rail"] = [
                dict(row) for row in cur.fetchall()
            ]

            # metro travel history
            cur.execute(
                """
                SELECT
                    trip_id,
                    schedule_id,
                    origin_station_id,
                    destination_station_id,
                    travel_date,
                    ticket_type,
                    amount_usd,
                    status
                FROM metro_travel_history
                WHERE user_id = %s
                ORDER BY travelled_at DESC
                """,
                (user_id,)
            )

            result["metro"] = [
                dict(row) for row in cur.fetchall()
            ]

    return result

def query_payment_info(booking_id: str) -> Optional[dict]:
    """Return payment record for a booking or metro trip."""

    sql = """
        SELECT
            payment_id,
            booking_id,
            amount_usd,
            method,
            status,
            paid_at
        FROM payments
        WHERE booking_id = %s
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            cur.execute(sql, (booking_id,))
            result = cur.fetchone()

            if result:
                return dict(result)

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

    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

                cur.execute(
                    """
                    SELECT *
                    FROM national_rail_schedules
                    WHERE schedule_id = %s
                    """,
                    (schedule_id,)
                )

                schedule = cur.fetchone()

                if not schedule:
                    return False, "Schedule not found"

                stops = schedule["stops_in_order"]

                if origin_station_id not in stops or destination_station_id not in stops:
                    return False, "Origin or destination is not on this schedule"

                origin_index = stops.index(origin_station_id)
                destination_index = stops.index(destination_station_id)

                if origin_index >= destination_index:
                    return False, "Invalid direction for this schedule"

                stops_travelled = destination_index - origin_index

                available_seats = query_available_seats(
                    schedule_id,
                    travel_date,
                    fare_class
                )

                if not available_seats:
                    return False, "No available seats"

                if seat_id == "any":
                    selected = auto_select_adjacent_seats(available_seats, 1)
                    seat_id = selected[0]
                else:
                    available_ids = {seat["seat_id"] for seat in available_seats}
                    if seat_id not in available_ids:
                        return False, "Selected seat is not available"

                selected_seat = next(
                    seat for seat in available_seats
                    if seat["seat_id"] == seat_id
                )

                fare = query_national_rail_fare(
                    schedule_id,
                    fare_class,
                    stops_travelled
                )

                if not fare:
                    return False, "Could not calculate fare"

                booking_id = _gen_booking_id()
                payment_id = _gen_payment_id()

                departure_time = schedule["first_train_time"]
                amount_usd = fare["total_fare_usd"]

                cur.execute(
                    """
                    INSERT INTO national_rail_bookings (
                        booking_id,
                        user_id,
                        schedule_id,
                        origin_station_id,
                        destination_station_id,
                        travel_date,
                        departure_time,
                        ticket_type,
                        fare_class,
                        coach,
                        seat_id,
                        stops_travelled,
                        amount_usd,
                        status,
                        booked_at,
                        travelled_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, 'confirmed',
                        NOW(), NULL
                    )
                    """,
                    (
                        booking_id,
                        user_id,
                        schedule_id,
                        origin_station_id,
                        destination_station_id,
                        travel_date,
                        departure_time,
                        ticket_type,
                        fare_class,
                        selected_seat["coach"],
                        seat_id,
                        stops_travelled,
                        amount_usd,
                    )
                )

                cur.execute(
                    """
                    INSERT INTO payments (
                        payment_id,
                        booking_id,
                        amount_usd,
                        method,
                        status,
                        paid_at
                    )
                    VALUES (%s, %s, %s, %s, %s, NOW())
                    """,
                    (
                        payment_id,
                        booking_id,
                        amount_usd,
                        "credit_card",
                        "paid",
                    )
                )

                return True, {
                    "booking_id": booking_id,
                    "payment_id": payment_id,
                    "user_id": user_id,
                    "schedule_id": schedule_id,
                    "origin_station_id": origin_station_id,
                    "destination_station_id": destination_station_id,
                    "travel_date": travel_date,
                    "seat_id": seat_id,
                    "coach": selected_seat["coach"],
                    "fare_class": fare_class,
                    "amount_usd": amount_usd,
                    "status": "confirmed",
                }

    except Exception as e:
        return False, str(e)
    
    
def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:

    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

                cur.execute(
                    """
                    SELECT
                        b.*,
                        s.service_type
                    FROM national_rail_bookings b
                    JOIN national_rail_schedules s
                        ON b.schedule_id = s.schedule_id
                    WHERE b.booking_id = %s
                    AND b.user_id = %s
                    """,
                    (booking_id, user_id)
                )

                booking = cur.fetchone()

                if not booking:
                    return False, "Booking not found"

                if booking["status"] == "cancelled":
                    return False, "Booking is already cancelled"

                travel_datetime = datetime.combine(
                    booking["travel_date"],
                    booking["departure_time"]
                ).replace(tzinfo=timezone.utc)

                now = datetime.now(timezone.utc)

                hours_before = (travel_datetime - now).total_seconds() / 3600

                amount = float(booking["amount_usd"])
                service_type = booking["service_type"]

                if service_type == "express":
                    if hours_before >= 24:
                        refund_rate = 1.00
                        policy_note = "Express service: 100% refund more than 24 hours before departure."
                    elif hours_before >= 2:
                        refund_rate = 0.50
                        policy_note = "Express service: 50% refund between 2 and 24 hours before departure."
                    else:
                        refund_rate = 0.00
                        policy_note = "Express service: no refund less than 2 hours before departure."
                else:
                    if hours_before >= 24:
                        refund_rate = 1.00
                        policy_note = "Normal service: 100% refund more than 24 hours before departure."
                    elif hours_before >= 12:
                        refund_rate = 0.75
                        policy_note = "Normal service: 75% refund between 12 and 24 hours before departure."
                    elif hours_before >= 2:
                        refund_rate = 0.50
                        policy_note = "Normal service: 50% refund between 2 and 12 hours before departure."
                    else:
                        refund_rate = 0.00
                        policy_note = "Normal service: no refund less than 2 hours before departure."

                refund_amount = round(amount * refund_rate, 2)

                cur.execute(
                    """
                    UPDATE national_rail_bookings
                    SET status = 'cancelled'
                    WHERE booking_id = %s
                    """,
                    (booking_id,)
                )

                cur.execute(
                    """
                    UPDATE payments
                    SET status = 'refunded'
                    WHERE booking_id = %s
                    """,
                    (booking_id,)
                )

                return True, {
                    "booking_id": booking_id,
                    "refund_amount_usd": refund_amount,
                    "refund_rate": refund_rate,
                    "policy_note": policy_note,
                    "status": "cancelled",
                }

    except Exception as e:
        return False, str(e)


# ── AUTHENTICATION QUERIES ────────────────────────────────────────────────────

def register_user(
    email: str,
    first_name: str,
    surname: str,
    year_of_birth: int,
    password: str,
    secret_question: str,
    secret_answer: str,
) -> tuple[bool, str]:

    full_name = f"{first_name} {surname}"

    user_id = "RU" + "".join(random.choices(string.digits, k=4))

    password_hash = ph.hash(password)

    try:
        with _connect() as conn:
            with conn.cursor() as cur:

                # email exists check
                cur.execute(
                    """
                    SELECT 1
                    FROM registered_users
                    WHERE email = %s
                    """,
                    (email,)
                )

                if cur.fetchone():
                    return (False, "Email already registered")

                # insert user
                cur.execute(
                    """
                    INSERT INTO registered_users (
                        user_id,
                        full_name,
                        email,
                        date_of_birth,
                        secret_question,
                        secret_answer,
                        registered_at,
                        is_active
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, NOW(), TRUE)
                    """,
                    (
                        user_id,
                        full_name,
                        email,
                        f"{year_of_birth}-01-01",
                        secret_question,
                        secret_answer,
                    )
                )

                # insert credentials
                cur.execute(
                    """
                    INSERT INTO user_credentials (
                        user_id,
                        password_hash
                    )
                    VALUES (%s, %s)
                    """,
                    (
                        user_id,
                        password_hash,
                    )
                )

        return (True, user_id)

    except Exception as e:
        return (False, str(e))
    

def login_user(email: str, password: str) -> Optional[dict]:

    sql = """
        SELECT
            ru.user_id,
            ru.email,
            ru.full_name,
            ru.phone,
            ru.date_of_birth,
            ru.is_active,
            uc.password_hash
        FROM registered_users ru
        JOIN user_credentials uc
            ON ru.user_id = uc.user_id
        WHERE ru.email = %s
    """

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            cur.execute(sql, (email,))
            user = cur.fetchone()

            if not user:
                return None

            try:
                ph.verify(user["password_hash"], password)

                return {
                    "user_id": user["user_id"],
                    "email": user["email"],
                    "full_name": user["full_name"],
                    "phone": user["phone"],
                    "date_of_birth": user["date_of_birth"],
                    "is_active": user["is_active"],
                }

            except VerifyMismatchError:
                return None

def get_user_secret_question(email: str) -> Optional[str]:
    """Return the secret question for a registered email."""

    sql = """
        SELECT secret_question
        FROM registered_users
        WHERE email = %s
    """

    with _connect() as conn:
        with conn.cursor() as cur:

            cur.execute(sql, (email,))
            row = cur.fetchone()

            if row:
                return row[0]

            return None

def verify_secret_answer(email: str, answer: str) -> bool:
    """
    Return True if the provided answer matches the stored secret answer.
    """

    sql = """
        SELECT secret_answer
        FROM registered_users
        WHERE email = %s
    """

    with _connect() as conn:
        with conn.cursor() as cur:

            cur.execute(sql, (email,))
            row = cur.fetchone()

            if not row:
                return False

            stored_answer = row[0]

            return stored_answer.strip().lower() == answer.strip().lower()
        
def update_password(email: str, new_password: str) -> bool:
    """
    Update the password for a user.
    """

    new_hash = ph.hash(new_password)

    sql = """
        UPDATE user_credentials
        SET password_hash = %s
        WHERE user_id = (
            SELECT user_id
            FROM registered_users
            WHERE email = %s
        )
    """

    with _connect() as conn:
        with conn.cursor() as cur:

            cur.execute(sql, (new_hash, email))

            return cur.rowcount > 0


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
