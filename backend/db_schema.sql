-- FundGuard database schema
-- Maps directly onto the pipeline: customers -> mandates -> cycle_events (risk+action+outcome) -> messages

CREATE TABLE IF NOT EXISTS customers (
    customer_id         VARCHAR(20) PRIMARY KEY,
    baseline_balance     NUMERIC(12,2) NOT NULL,
    balance_volatility   NUMERIC(6,4) NOT NULL,  -- 'cv' from the generator
    salary_day           SMALLINT NOT NULL CHECK (salary_day BETWEEN 1 AND 28),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mandates (
    mandate_id            SERIAL PRIMARY KEY,
    customer_id           VARCHAR(20) NOT NULL REFERENCES customers(customer_id),
    mandate_name           VARCHAR(100) NOT NULL,
    mandate_amount          NUMERIC(12,2) NOT NULL,
    debit_day               SMALLINT NOT NULL CHECK (debit_day BETWEEN 1 AND 28),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Core event table: one row per mandate per billing cycle. This is the
-- primary audit-trail record - every risk score, decision, and outcome.
CREATE TABLE IF NOT EXISTS cycle_events (
    event_id                 SERIAL PRIMARY KEY,
    mandate_id                INTEGER NOT NULL REFERENCES mandates(mandate_id),
    cycle_number                SMALLINT NOT NULL,

    -- Phase 2: risk model output
    risk_score                   NUMERIC(6,4) NOT NULL,
    risk_band                     VARCHAR(20) NOT NULL,

    -- Phase 3: predictive action decided pre-debit
    predictive_action              VARCHAR(50) NOT NULL,
    reason_code                     TEXT,

    -- Ground truth outcome (in production this comes from the actual
    -- payment gateway callback; here it's the synthetic label)
    actual_outcome_failed             BOOLEAN NOT NULL,

    -- Phase 4: reactive triage, only populated if actual_outcome_failed = true
    triage_category                    VARCHAR(50),
    retry_scheduled                     BOOLEAN,
    retry_expected_value                 NUMERIC(10,2),

    created_at                            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Phase 5: every LLM-generated message, tied back to the event that triggered it
CREATE TABLE IF NOT EXISTS messages (
    message_id       SERIAL PRIMARY KEY,
    event_id           INTEGER NOT NULL REFERENCES cycle_events(event_id),
    action_type          VARCHAR(50) NOT NULL,
    tone                   VARCHAR(20) NOT NULL,  -- 'english' or 'hinglish'
    message_text             TEXT NOT NULL,
    generated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cycle_events_mandate ON cycle_events(mandate_id);
CREATE INDEX IF NOT EXISTS idx_messages_event ON messages(event_id);