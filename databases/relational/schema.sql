-- ============================================================
--  TransitFlow PostgreSQL Schema
--  Seed data is loaded separately by: python skeleton/seed_postgres.py
--
--  TWO ROLES:
--    1. Relational  → dual-network transit data you design below
--    2. Vector      → policy documents for RAG (provided — do not modify)
-- ============================================================

-- ============================================================
--  STUDENT TASK — Design and create your relational tables here
--
--  Start from the mock data in train-mock-data/:
--    metro_stations.json, national_rail_stations.json
--    metro_schedules.json, national_rail_schedules.json
--    national_rail_seat_layouts.json
--    registered_users.json
--    bookings.json, metro_travel_history.json
--    payments.json, feedback.json
--
--  Think about:
--    - What tables do you need?
--    - What columns and data types?
--    - Which fields are primary keys? Which are foreign keys?
--    - What constraints make sense?
--
--  Apply your schema with:
--    docker-compose down -v && docker-compose up -d
-- ============================================================

CREATE TABLE IF NOT EXISTS national_rail_stations (
    station_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lines TEXT[] NOT NULL,
    is_interchange_national_rail BOOLEAN NOT NULL,
    interchange_national_rail_lines TEXT[],
    is_interchange_metro BOOLEAN NOT NULL,
    interchange_metro_station_id TEXT
);

CREATE TABLE IF NOT EXISTS national_rail_links (
    from_station_id TEXT NOT NULL,
    to_station_id TEXT NOT NULL,
    line TEXT NOT NULL,
    travel_time_min INTEGER NOT NULL,
    PRIMARY KEY (from_station_id, to_station_id, line),
    FOREIGN KEY (from_station_id) REFERENCES national_rail_stations(station_id),
    FOREIGN KEY (to_station_id) REFERENCES national_rail_stations(station_id)
);

CREATE TABLE IF NOT EXISTS metro_stations (
    station_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lines TEXT[] NOT NULL,
    is_interchange_metro BOOLEAN NOT NULL,
    interchange_metro_lines TEXT[],
    is_interchange_national_rail BOOLEAN NOT NULL,
    interchange_national_rail_station_id TEXT,
    FOREIGN KEY (interchange_national_rail_station_id)
        REFERENCES national_rail_stations(station_id)
);

CREATE TABLE IF NOT EXISTS metro_links (
    from_station_id TEXT NOT NULL,
    to_station_id TEXT NOT NULL,
    line TEXT NOT NULL,
    travel_time_min INTEGER NOT NULL,
    PRIMARY KEY (from_station_id, to_station_id, line),
    FOREIGN KEY (from_station_id) REFERENCES metro_stations(station_id),
    FOREIGN KEY (to_station_id) REFERENCES metro_stations(station_id)
);

CREATE TABLE IF NOT EXISTS registered_users (
    user_id TEXT PRIMARY KEY,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    password TEXT NOT NULL,
    phone TEXT,
    date_of_birth DATE,
    secret_question TEXT,
    secret_answer TEXT,
    registered_at TIMESTAMPTZ NOT NULL,
    is_active BOOLEAN NOT NULL
);

CREATE TABLE IF NOT EXISTS national_rail_schedules (
    schedule_id TEXT PRIMARY KEY,
    line TEXT NOT NULL,
    service_type TEXT NOT NULL,
    direction TEXT NOT NULL,
    origin_station_id TEXT NOT NULL,
    destination_station_id TEXT NOT NULL,
    stops_in_order TEXT[] NOT NULL,
    passed_through_stations TEXT[],
    first_train_time TIME NOT NULL,
    last_train_time TIME NOT NULL,
    travel_time_from_origin_min JSONB NOT NULL,
    fare_classes JSONB NOT NULL,
    frequency_min INTEGER NOT NULL,
    operates_on TEXT[] NOT NULL,
    FOREIGN KEY (origin_station_id) REFERENCES national_rail_stations(station_id),
    FOREIGN KEY (destination_station_id) REFERENCES national_rail_stations(station_id)
);

CREATE TABLE IF NOT EXISTS metro_schedules (
    schedule_id TEXT PRIMARY KEY,
    line TEXT NOT NULL,
    direction TEXT NOT NULL,
    origin_station_id TEXT NOT NULL,
    destination_station_id TEXT NOT NULL,
    stops_in_order TEXT[] NOT NULL,
    first_train_time TIME NOT NULL,
    last_train_time TIME NOT NULL,
    travel_time_from_origin_min JSONB NOT NULL,
    base_fare_usd NUMERIC(6,2) NOT NULL,
    per_stop_rate_usd NUMERIC(6,2) NOT NULL,
    frequency_min INTEGER NOT NULL,
    operates_on TEXT[] NOT NULL,
    FOREIGN KEY (origin_station_id) REFERENCES metro_stations(station_id),
    FOREIGN KEY (destination_station_id) REFERENCES metro_stations(station_id)
);

CREATE TABLE IF NOT EXISTS national_rail_seat_layouts (
    layout_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL,
    coaches JSONB NOT NULL,
    FOREIGN KEY (schedule_id)
        REFERENCES national_rail_schedules(schedule_id)
);

CREATE TABLE IF NOT EXISTS national_rail_bookings (
    booking_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    schedule_id TEXT NOT NULL,
    origin_station_id TEXT NOT NULL,
    destination_station_id TEXT NOT NULL,
    travel_date DATE NOT NULL,
    departure_time TIME NOT NULL,
    ticket_type TEXT NOT NULL,
    fare_class TEXT NOT NULL,
    coach TEXT,
    seat_id TEXT,
    stops_travelled INTEGER NOT NULL,
    amount_usd NUMERIC(8,2) NOT NULL,
    status TEXT NOT NULL,
    booked_at TIMESTAMPTZ NOT NULL,
    travelled_at TIMESTAMPTZ,
    FOREIGN KEY (user_id) REFERENCES registered_users(user_id),
    FOREIGN KEY (schedule_id) REFERENCES national_rail_schedules(schedule_id),
    FOREIGN KEY (origin_station_id) REFERENCES national_rail_stations(station_id),
    FOREIGN KEY (destination_station_id) REFERENCES national_rail_stations(station_id)
);

CREATE TABLE IF NOT EXISTS metro_travel_history (
    trip_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    schedule_id TEXT NOT NULL,
    origin_station_id TEXT NOT NULL,
    destination_station_id TEXT NOT NULL,
    travel_date DATE NOT NULL,
    ticket_type TEXT NOT NULL,
    day_pass_ref TEXT,
    stops_travelled INTEGER,
    amount_usd NUMERIC(8,2) NOT NULL,
    status TEXT NOT NULL,
    purchased_at TIMESTAMPTZ,
    travelled_at TIMESTAMPTZ,
    FOREIGN KEY (user_id) REFERENCES registered_users(user_id),
    FOREIGN KEY (schedule_id) REFERENCES metro_schedules(schedule_id),
    FOREIGN KEY (origin_station_id) REFERENCES metro_stations(station_id),
    FOREIGN KEY (destination_station_id) REFERENCES metro_stations(station_id),
    FOREIGN KEY (day_pass_ref) REFERENCES metro_travel_history(trip_id)
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id TEXT PRIMARY KEY,
    booking_id TEXT NOT NULL,
    amount_usd NUMERIC(8,2) NOT NULL,
    method TEXT NOT NULL,
    status TEXT NOT NULL,
    paid_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id TEXT PRIMARY KEY,
    booking_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment TEXT,
    submitted_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (user_id) REFERENCES registered_users(user_id)
);

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

-- Index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS policy_documents_embedding_idx
ON policy_documents
USING hnsw (embedding vector_cosine_ops);