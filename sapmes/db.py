"""Database connection helpers.

Settings come from MES_DB_* environment variables. A .env file in the project
root is loaded automatically (see .env.example).
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SQL_DIR = ROOT / "sql"
load_dotenv(ROOT / ".env")


def _add_windows_libpq() -> None:
    """Let psycopg's pure-Python driver find libpq.dll from a local PostgreSQL install.

    Needed when the psycopg-binary DLL is unavailable or blocked (for example by
    Windows Smart App Control). Set PSYCOPG_IMPL=python in .env in that case.
    """
    if os.name != "nt":
        return
    base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PostgreSQL"
    for bin_dir in sorted(base.glob("*/bin"), key=lambda p: p.parent.name, reverse=True):
        if (bin_dir / "libpq.dll").exists():
            os.add_dll_directory(str(bin_dir))
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
            return


_add_windows_libpq()

import psycopg  # noqa: E402  (after the libpq path is set)
from psycopg import sql  # noqa: E402

DEFAULT_DB = "sap_mes_validation"


def conn_params(dbname: str | None = None) -> dict:
    return {
        "host": os.getenv("MES_DB_HOST", "localhost"),
        "port": int(os.getenv("MES_DB_PORT", "5432")),
        "user": os.getenv("MES_DB_USER", "postgres"),
        "password": os.getenv("MES_DB_PASSWORD") or None,
        "dbname": dbname or os.getenv("MES_DB_NAME", DEFAULT_DB),
        "options": "-c TimeZone=UTC",  # reports and week boundaries in UTC
    }


def ensure_database(dbname: str) -> None:
    """Create the database if it does not exist yet."""
    with psycopg.connect(**conn_params("postgres"), autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone():
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))


def connect(dbname: str | None = None) -> psycopg.Connection:
    return psycopg.connect(**conn_params(dbname))


def run_sql_files(conn: psycopg.Connection) -> None:
    """Apply every sql/NN_*.sql file in order (drops and recreates the schema)."""
    for path in sorted(SQL_DIR.glob("[0-9][0-9]_*.sql")):
        conn.execute(path.read_text(encoding="utf-8"))
