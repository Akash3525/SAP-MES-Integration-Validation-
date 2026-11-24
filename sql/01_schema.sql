-- SAP PP <-> MES integration: simulated source tables.
-- Table and column names are simplified for teaching purposes. They are NOT the
-- real SAP tables (AUFK, AFKO, AFRU, RESB ...) and this is not a live SAP link.

DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;

-- Reference time for all checks, so results are reproducible.
CREATE TABLE validation_settings (
    id                     int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    as_of                  timestamptz NOT NULL,
    qty_tolerance          numeric NOT NULL DEFAULT 0.001,  -- absolute, in base unit
    consumption_tolerance  numeric NOT NULL DEFAULT 0.05,   -- 5 % relative
    stuck_after_minutes    int     NOT NULL DEFAULT 60,
    max_retries            int     NOT NULL DEFAULT 3,
    mes_grace_hours        int     NOT NULL DEFAULT 24      -- time MES has to receive a new order
);

---------------------------------------------------------------- SAP side
CREATE TABLE sap_production_orders (
    order_no       varchar(12) PRIMARY KEY,
    material_no    varchar(18) NOT NULL,
    plant          varchar(4)  NOT NULL,
    planned_qty    numeric(12,3) NOT NULL CHECK (planned_qty > 0),
    uom            varchar(3)  NOT NULL,
    sap_status     varchar(4)  NOT NULL CHECK (sap_status IN ('REL', 'CNF', 'TECO')),
    planned_start  timestamptz NOT NULL,
    planned_end    timestamptz NOT NULL,
    created_at     timestamptz NOT NULL
);

CREATE TABLE sap_order_components (
    order_no            varchar(12) NOT NULL REFERENCES sap_production_orders,
    component_material  varchar(18) NOT NULL,
    required_qty        numeric(12,3) NOT NULL CHECK (required_qty > 0),
    uom                 varchar(3) NOT NULL,
    PRIMARY KEY (order_no, component_material)
);

CREATE TABLE sap_confirmations (
    conf_id       serial PRIMARY KEY,
    order_no      varchar(12) NOT NULL REFERENCES sap_production_orders,
    yield_qty     numeric(12,3) NOT NULL,
    scrap_qty     numeric(12,3) NOT NULL DEFAULT 0,
    confirmed_at  timestamptz NOT NULL
);

---------------------------------------------------------------- MES side
-- No unique key on order_no on purpose: duplicate downloads are one of the
-- problems the checks have to catch.
CREATE TABLE mes_work_orders (
    mes_id       serial PRIMARY KEY,
    order_no     varchar(12) NOT NULL,
    material_no  varchar(18) NOT NULL,
    planned_qty  numeric(12,3) NOT NULL,
    mes_status   varchar(12) NOT NULL CHECK (mes_status IN ('RELEASED', 'IN_PROGRESS', 'COMPLETE')),
    line         varchar(10) NOT NULL,
    received_at  timestamptz NOT NULL
);

CREATE TABLE mes_production_records (
    record_id              serial PRIMARY KEY,
    order_no               varchar(12) NOT NULL,
    line                   varchar(10) NOT NULL,
    good_qty               numeric(12,3) NOT NULL,
    scrap_qty              numeric(12,3) NOT NULL DEFAULT 0,
    start_ts               timestamptz NOT NULL,
    end_ts                 timestamptz NOT NULL,
    planned_runtime_min    numeric(8,1) NOT NULL,
    actual_runtime_min     numeric(8,1) NOT NULL,
    ideal_cycle_time_sec   numeric(8,3) NOT NULL
);

CREATE TABLE mes_material_consumption (
    order_no            varchar(12) NOT NULL,
    component_material  varchar(18) NOT NULL,
    consumed_qty        numeric(12,3) NOT NULL,
    recorded_at         timestamptz NOT NULL,
    PRIMARY KEY (order_no, component_material)
);

---------------------------------------------------------------- Interface
-- IDoc / API style message log. LOIPRO = order download, CONF = confirmation
-- upload, MBGMCR = goods-issue upload. Each retry is a new row.
CREATE TABLE interface_messages (
    msg_id           serial PRIMARY KEY,
    order_no         varchar(12) NOT NULL,
    direction        varchar(10) NOT NULL CHECK (direction IN ('SAP_TO_MES', 'MES_TO_SAP')),
    msg_type         varchar(8)  NOT NULL CHECK (msg_type IN ('LOIPRO', 'CONF', 'MBGMCR')),
    status           varchar(10) NOT NULL CHECK (status IN ('PROCESSED', 'ERROR', 'PENDING')),
    retry_count      int NOT NULL DEFAULT 0,
    error_text       text,
    created_at       timestamptz NOT NULL,
    last_attempt_at  timestamptz NOT NULL
);

---------------------------------------------------------------- Ground truth
-- Written by the generator: which order received which deliberate problem.
-- Only the tests read it; the checks never do.
CREATE TABLE injected_issues (
    order_no    varchar(12) PRIMARY KEY,
    check_name  text NOT NULL
);

CREATE INDEX ON mes_work_orders (order_no);
CREATE INDEX ON mes_production_records (order_no);
CREATE INDEX ON sap_confirmations (order_no);
CREATE INDEX ON interface_messages (order_no, msg_type);
