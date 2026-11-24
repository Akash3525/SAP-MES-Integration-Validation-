"""Prove each check finds exactly the injected problems: no misses, no false alarms."""
import pytest

from sapmes.export_reports import EXPORTS, export_all
from sapmes.generate_data import ISSUE_COUNTS, N_ORDERS

CHECK_VIEWS = {
    "missing_in_mes": "v_chk_missing_in_mes",
    "no_sap_confirmation": "v_chk_no_sap_confirmation",
    "duplicate_mes": "v_chk_duplicate_mes",
    "qty_mismatch": "v_chk_qty_mismatch",
    "consumption_mismatch": "v_chk_consumption_mismatch",
    "status_mismatch": "v_chk_status_mismatch",
    "stuck_messages": "v_chk_stuck_messages",
}


@pytest.mark.parametrize("check", list(CHECK_VIEWS))
def test_check_finds_exactly_injected_orders(conn, injected, check):
    found = {r[0] for r in conn.execute(f"SELECT order_no FROM {CHECK_VIEWS[check]}")}
    expected = injected[check]
    assert len(expected) == ISSUE_COUNTS[check]
    assert found - expected == set(), f"false alarms: {sorted(found - expected)}"
    assert expected - found == set(), f"missed: {sorted(expected - found)}"


def test_generator_injects_57_problems_on_distinct_orders(conn):
    total, distinct = conn.execute(
        "SELECT count(*), count(DISTINCT order_no) FROM injected_issues").fetchone()
    assert total == distinct == sum(ISSUE_COUNTS.values()) == 57
    assert conn.execute("SELECT count(*) FROM sap_production_orders").fetchone()[0] == N_ORDERS


def test_recent_orders_inside_grace_period_are_not_flagged(conn):
    # Orders created within the last 24 h and not yet in MES are normal, not findings.
    recent_missing = conn.execute("""
        SELECT count(*) FROM sap_production_orders o, validation_settings s
        WHERE o.created_at >= s.as_of - make_interval(hours => s.mes_grace_hours)
          AND NOT EXISTS (SELECT 1 FROM mes_work_orders m WHERE m.order_no = o.order_no)
    """).fetchone()[0]
    assert recent_missing > 0, "test data should contain in-flight orders"
    flagged = conn.execute("""
        SELECT count(*) FROM v_validation_findings f
        JOIN sap_production_orders o USING (order_no), validation_settings s
        WHERE o.created_at >= s.as_of - make_interval(hours => s.mes_grace_hours)
    """).fetchone()[0]
    assert flagged == 0


def test_retried_then_processed_messages_are_not_stuck(conn):
    # Transient errors that later succeeded exist in the log but must not be findings.
    recovered = {r[0] for r in conn.execute("""
        SELECT DISTINCT e.order_no FROM interface_messages e
        JOIN interface_messages p ON p.order_no = e.order_no AND p.msg_type = e.msg_type
         AND p.direction = e.direction AND p.msg_id > e.msg_id AND p.status = 'PROCESSED'
        WHERE e.status = 'ERROR'
    """)}
    assert recovered, "test data should contain recovered transient errors"
    stuck = {r[0] for r in conn.execute("SELECT order_no FROM v_chk_stuck_messages")}
    injected_stuck = {r[0] for r in conn.execute(
        "SELECT order_no FROM injected_issues WHERE check_name = 'stuck_messages'")}
    assert (recovered - injected_stuck) & stuck == set()


def test_reporting_views_have_sane_values(conn):
    oee = conn.execute("SELECT availability, performance, quality, oee FROM v_oee_weekly").fetchall()
    assert oee
    for row in oee:
        assert all(0 < v <= 1 for v in row), row
    health = conn.execute("SELECT attempts, processed + errors + pending, success_rate "
                          "FROM v_interface_health_weekly").fetchall()
    assert health
    for attempts, parts, rate in health:
        assert attempts == parts and 0 <= rate <= 1


def test_export_writes_four_csv_files(conn, tmp_path):
    paths = export_all(conn, tmp_path)
    assert sorted(p.name for p in paths) == sorted(EXPORTS)
    summary = (tmp_path / "validation_summary.csv").read_text(encoding="utf-8").splitlines()
    assert summary[0] == "check_name,findings"
    assert len(summary) == 1 + len(CHECK_VIEWS)
