-- AI request traces for the AI Engineering Lab. Owner decision (2026-10-11): store stages, IDs and timings
-- only, never the question or prompt text, and keep traces for 30 days. Written by piq_trace_writer (this
-- table only, see scripts/lab_setup.py); read by the API's read-only role.
CREATE TABLE request_traces (
    trace_id             uuid PRIMARY KEY,
    created_at           timestamptz NOT NULL DEFAULT now(),
    username             text NOT NULL,
    funds                text[] NOT NULL,
    endpoint             text NOT NULL,
    period               date,
    intent               text NOT NULL,
    answer_source        text NOT NULL,
    model                text,
    verification_status  text NOT NULL,
    numbers_checked      integer NOT NULL DEFAULT 0,
    unsupported_numbers  integer NOT NULL DEFAULT 0,
    cited_property_ids   text[] NOT NULL DEFAULT '{}',
    source_record_ids    bigint[] NOT NULL DEFAULT '{}',
    facts_sha256         text,                -- proves which verified facts were sent, without storing them
    question_chars       integer NOT NULL,    -- length only; the text itself is never stored
    prompt_tokens        integer,
    output_tokens        integer,
    model_ms             integer,
    total_ms             integer NOT NULL,
    stages               jsonb NOT NULL,
    notes                text[] NOT NULL DEFAULT '{}'
);

CREATE INDEX request_traces_created_idx ON request_traces (created_at DESC);
CREATE INDEX request_traces_user_idx ON request_traces (username, created_at DESC);
