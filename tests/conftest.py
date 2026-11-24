import os

import pytest

from sapmes import db
from sapmes.generate_data import build_database

TEST_DB = os.getenv("MES_TEST_DB_NAME", "sap_mes_validation_test")


@pytest.fixture(scope="session")
def conn():
    """A dedicated test database, rebuilt from scratch once per test run."""
    db.ensure_database(TEST_DB)
    with db.connect(TEST_DB) as c:
        build_database(c)
        yield c


@pytest.fixture(scope="session")
def injected(conn):
    rows = conn.execute("SELECT order_no, check_name FROM injected_issues").fetchall()
    by_check: dict[str, set[str]] = {}
    for order_no, check in rows:
        by_check.setdefault(check, set()).add(order_no)
    return by_check
