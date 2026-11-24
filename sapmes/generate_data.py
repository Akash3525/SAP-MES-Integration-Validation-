"""Generate simulated SAP PP and MES data with known, injected problems.

Usage:  python -m sapmes.generate_data

The generator first builds a clean, internally consistent data set for
N_ORDERS production orders. It then injects ISSUE_COUNTS problems, one per
order, each designed to trip exactly one validation check, and records them
in injected_issues so the tests can prove every check finds exactly those
orders. All data is fictional and deterministic for a given seed.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sapmes import db

AS_OF = datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)
N_ORDERS = 400
N_RECENT = 6  # orders created inside MES's grace period (must NOT be flagged)

ISSUE_COUNTS = {
    "missing_in_mes": 8,
    "no_sap_confirmation": 9,
    "duplicate_mes": 6,
    "qty_mismatch": 10,
    "consumption_mismatch": 8,
    "status_mismatch": 7,
    "stuck_messages": 9,
}

# Finished good -> (ideal cycle time in seconds per unit, BOM in kg per unit)
PRODUCTS = {
    "FG-100010": (0.060, {"API-200010": 0.000500, "EXC-300010": 0.000120, "EXC-300020": 0.000030}),
    "FG-100020": (0.075, {"API-200020": 0.000250, "EXC-300010": 0.000200, "EXC-300030": 0.000015}),
    "FG-100030": (0.050, {"API-200030": 0.000100, "EXC-300040": 0.000300}),
    "FG-100040": (0.090, {"API-200040": 0.000400, "EXC-300010": 0.000150, "EXC-300020": 0.000025}),
}
LINES = ["LINE-01", "LINE-02", "LINE-03"]
PLANT = "1000"

TRANSIENT_ERRORS = [
    "Lock on order by user BATCHJOB, retry scheduled",
    "Connection timeout to MES endpoint after 30 s",
    "HTTP 503 Service Unavailable",
]
FATAL_ERRORS = [
    "Deficit of unrestricted-use stock for component",
    "Order is locked for confirmation (status TECO)",
    "Material master not extended to plant 1000",
    "Posting period 2026/11 not open",
]


@dataclass
class Order:
    order_no: str
    material: str
    planned_qty: int
    created_at: datetime
    state: str  # NEW, RELEASED, IN_PROGRESS, COMPLETE
    line: str
    sap_status: str = "REL"
    mes_rows: list[dict] = field(default_factory=list)
    production: dict | None = None
    consumption: dict[str, float] = field(default_factory=dict)
    components: dict[str, float] = field(default_factory=dict)
    confirmation: dict | None = None
    messages: list[dict] = field(default_factory=list)


def _msg(order: Order, direction: str, msg_type: str, created: datetime, rng: random.Random,
         final_status: str = "PROCESSED", failed_attempts: int | None = None,
         final_error: str | None = None, last_attempt: datetime | None = None) -> None:
    """Append one logical message to the log, including failed earlier attempts."""
    if failed_attempts is None:
        failed_attempts = 1 if rng.random() < 0.12 else 0  # occasional transient error
    ts = created
    for attempt in range(failed_attempts):
        order.messages.append(dict(direction=direction, msg_type=msg_type, status="ERROR",
                                   retry_count=attempt, error_text=rng.choice(TRANSIENT_ERRORS),
                                   created_at=created, last_attempt_at=ts))
        ts = ts + timedelta(minutes=rng.randint(5, 30))
    order.messages.append(dict(direction=direction, msg_type=msg_type, status=final_status,
                               retry_count=failed_attempts, error_text=final_error,
                               created_at=created, last_attempt_at=last_attempt or ts))


def _build_clean_orders(rng: random.Random) -> list[Order]:
    ages = sorted((rng.uniform(1.5, 56 * 24) for _ in range(N_ORDERS - N_RECENT)), reverse=True)
    ages += sorted((rng.uniform(1.0, 20.0) for _ in range(N_RECENT)), reverse=True)

    orders = []
    for i, age_h in enumerate(ages):
        material = rng.choice(list(PRODUCTS))
        created = AS_OF - timedelta(hours=age_h)
        if i >= N_ORDERS - N_RECENT:
            state = "NEW"
        else:
            state = rng.choices(["RELEASED", "IN_PROGRESS", "COMPLETE"], [15, 15, 70])[0]
        o = Order(order_no=f"10{i + 1:08d}", material=material,
                  planned_qty=rng.randrange(50_000, 250_001, 1000), created_at=created,
                  state=state, line=rng.choice(LINES))
        cycle, bom = PRODUCTS[material]
        o.components = {c: round(per_unit * o.planned_qty, 3) for c, per_unit in bom.items()}

        if state == "NEW":
            # Inside the grace period: half are already in MES, half are still being sent.
            if i % 2 == 0:
                received = created + timedelta(minutes=rng.randint(5, 50))
                o.mes_rows.append(dict(status="RELEASED", received_at=received))
                _msg(o, "SAP_TO_MES", "LOIPRO", created, rng, failed_attempts=0, last_attempt=received)
            else:
                _msg(o, "SAP_TO_MES", "LOIPRO", created, rng, final_status="PENDING",
                     failed_attempts=1, last_attempt=AS_OF - timedelta(minutes=rng.randint(5, 40)))
            orders.append(o)
            continue

        received = created + timedelta(minutes=rng.randint(5, 120))
        _msg(o, "SAP_TO_MES", "LOIPRO", created, rng)

        if state == "COMPLETE":
            start = received + timedelta(hours=rng.uniform(2, 24))
            total = o.planned_qty * rng.uniform(0.97, 1.0)
            scrap = round(total * rng.uniform(0.0, 0.02))
            good = round(total) - scrap
            ideal_min = cycle * (good + scrap) / 60
            run_min = ideal_min / rng.uniform(0.75, 0.95)
            planned_min = run_min / rng.uniform(0.80, 0.95)
            end = start + timedelta(minutes=planned_min)
            if end + timedelta(hours=1) >= AS_OF:
                state = o.state = "IN_PROGRESS"
            else:
                o.production = dict(good=good, scrap=scrap, start=start, end=end,
                                     planned_min=round(planned_min, 1), run_min=round(run_min, 1),
                                     cycle=cycle)
                factor = (good + scrap) / o.planned_qty
                o.consumption = {c: round(req * factor * rng.uniform(0.98, 1.02), 3)
                                 for c, req in o.components.items()}
                o.confirmation = dict(yield_qty=good, scrap_qty=scrap,
                                      confirmed_at=end + timedelta(minutes=rng.randint(2, 30)))
                o.sap_status = "TECO" if rng.random() < 0.4 else "CNF"
                o.mes_rows.append(dict(status="COMPLETE", received_at=received))
                _msg(o, "MES_TO_SAP", "MBGMCR", end, rng)
                _msg(o, "MES_TO_SAP", "CONF", end, rng)

        if state in ("RELEASED", "IN_PROGRESS"):
            o.mes_rows.append(dict(status=state, received_at=received))
        orders.append(o)
    return orders


def _inject_issues(orders: list[Order], rng: random.Random) -> dict[str, str]:
    """Mutate selected orders so each trips exactly one check. Returns order_no -> check."""
    used: set[str] = set()
    issues: dict[str, str] = {}

    def pick(check: str, candidates: list[Order]) -> list[Order]:
        pool = [o for o in candidates if o.order_no not in used]
        chosen = rng.sample(pool, ISSUE_COUNTS[check])
        for o in chosen:
            used.add(o.order_no)
            issues[o.order_no] = check
        return chosen

    complete = [o for o in orders if o.state == "COMPLETE"]
    released = [o for o in orders if o.state == "RELEASED"]
    in_mes = [o for o in orders if o.state != "NEW" and o.mes_rows]

    # 1. LOIPRO shows as processed, but the order never reached MES.
    for o in pick("missing_in_mes", released):
        o.mes_rows.clear()

    # 2. MES finished the order but never sent the confirmation.
    for o in pick("no_sap_confirmation", complete):
        o.confirmation = None
        o.sap_status = "REL"
        o.messages = [m for m in o.messages if m["msg_type"] != "CONF"]

    # 3. Order downloaded twice (e.g. a manual IDoc reprocess).
    for o in pick("duplicate_mes", in_mes):
        first = o.mes_rows[0]
        o.mes_rows.append(dict(status=first["status"],
                               received_at=first["received_at"] + timedelta(minutes=rng.randint(10, 90))))
        _msg(o, "SAP_TO_MES", "LOIPRO", first["received_at"], rng, failed_attempts=0)

    # 4. SAP confirmation quantity differs from what MES reported.
    for o in pick("qty_mismatch", complete):
        delta = max(1, round(o.confirmation["yield_qty"] * rng.uniform(0.01, 0.05)))
        o.confirmation["yield_qty"] += rng.choice([-1, 1]) * delta

    # 5. One component consumed well outside the 5 % tolerance.
    for o in pick("consumption_mismatch", complete):
        comp = rng.choice(list(o.consumption))
        o.consumption[comp] = round(o.consumption[comp] * rng.choice([0.85, 1.15]), 3)

    # 6. Status out of sync: MES never closed the order, or SAP status not updated.
    for n, o in enumerate(pick("status_mismatch", complete)):
        if n % 2 == 0:
            o.mes_rows[0]["status"] = "IN_PROGRESS"
        else:
            o.sap_status = "REL"

    # 7. Goods-issue message stuck: pending for hours, or failed with no retries left.
    stuck_pool = [o for o in complete if o.production["end"] < AS_OF - timedelta(hours=3)]
    for n, o in enumerate(pick("stuck_messages", stuck_pool)):
        o.messages = [m for m in o.messages if m["msg_type"] != "MBGMCR"]
        end = o.production["end"]
        if n % 2 == 0:
            last = end + (AS_OF - timedelta(hours=2) - end) * rng.uniform(0.1, 1.0)
            _msg(o, "MES_TO_SAP", "MBGMCR", end, rng, final_status="PENDING",
                 failed_attempts=0, last_attempt=last)
        else:
            _msg(o, "MES_TO_SAP", "MBGMCR", end, rng, final_status="ERROR",
                 failed_attempts=3, final_error=rng.choice(FATAL_ERRORS))
    return issues


def _insert(conn, orders: list[Order], issues: dict[str, str]) -> None:
    cur = conn.cursor()
    cur.execute("INSERT INTO validation_settings (as_of) VALUES (%s)", (AS_OF,))
    cur.executemany(
        "INSERT INTO sap_production_orders VALUES (%s, %s, %s, %s, 'EA', %s, %s, %s, %s)",
        [(o.order_no, o.material, PLANT, o.planned_qty, o.sap_status,
          o.created_at + timedelta(days=1), o.created_at + timedelta(days=3), o.created_at)
         for o in orders])
    cur.executemany(
        "INSERT INTO sap_order_components VALUES (%s, %s, %s, 'KG')",
        [(o.order_no, c, q) for o in orders for c, q in o.components.items()])
    cur.executemany(
        "INSERT INTO sap_confirmations (order_no, yield_qty, scrap_qty, confirmed_at) VALUES (%s, %s, %s, %s)",
        [(o.order_no, o.confirmation["yield_qty"], o.confirmation["scrap_qty"], o.confirmation["confirmed_at"])
         for o in orders if o.confirmation])
    mes_rows = [(o.order_no, o.material, o.planned_qty, r["status"], o.line, r["received_at"])
                for o in orders for r in o.mes_rows]
    mes_rows.sort(key=lambda r: r[5])
    cur.executemany(
        "INSERT INTO mes_work_orders (order_no, material_no, planned_qty, mes_status, line, received_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)", mes_rows)
    cur.executemany(
        "INSERT INTO mes_production_records (order_no, line, good_qty, scrap_qty, start_ts, end_ts, "
        "planned_runtime_min, actual_runtime_min, ideal_cycle_time_sec) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        [(o.order_no, o.line, p["good"], p["scrap"], p["start"], p["end"], p["planned_min"], p["run_min"],
          p["cycle"]) for o in orders if (p := o.production)])
    cur.executemany(
        "INSERT INTO mes_material_consumption VALUES (%s, %s, %s, %s)",
        [(o.order_no, c, q, o.production["end"]) for o in orders for c, q in o.consumption.items()])
    msgs = [(o.order_no, m["direction"], m["msg_type"], m["status"], m["retry_count"], m["error_text"],
             m["created_at"], m["last_attempt_at"]) for o in orders for m in o.messages]
    msgs.sort(key=lambda m: (m[7], m[4]))  # msg_id follows attempt time
    cur.executemany(
        "INSERT INTO interface_messages (order_no, direction, msg_type, status, retry_count, error_text, "
        "created_at, last_attempt_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", msgs)
    cur.executemany("INSERT INTO injected_issues VALUES (%s, %s)", sorted(issues.items()))


def build_database(conn, seed: int = 42) -> dict[str, int]:
    """Recreate the schema and load a fresh data set. Returns issue counts."""
    rng = random.Random(seed)
    db.run_sql_files(conn)
    orders = _build_clean_orders(rng)
    issues = _inject_issues(orders, rng)
    _insert(conn, orders, issues)
    conn.commit()
    counts: dict[str, int] = {}
    for check in issues.values():
        counts[check] = counts.get(check, 0) + 1
    return counts


def main() -> None:
    name = db.conn_params()["dbname"]
    db.ensure_database(name)
    with db.connect() as conn:
        counts = build_database(conn)
        n_orders = conn.execute("SELECT count(*) FROM sap_production_orders").fetchone()[0]
        n_msgs = conn.execute("SELECT count(*) FROM interface_messages").fetchone()[0]
    print(f"Database '{name}': {n_orders} orders, {n_msgs} interface messages")
    print(f"Injected {sum(counts.values())} problems:")
    for check in ISSUE_COUNTS:
        print(f"  {check:<22} {counts.get(check, 0)}")


if __name__ == "__main__":
    main()
