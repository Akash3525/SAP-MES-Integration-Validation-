"""Export the reporting views to CSV files for Power BI.

Usage:  python -m sapmes.export_reports
Writes four files to exports/.
"""
import csv

from sapmes import db

EXPORT_DIR = db.ROOT / "exports"
EXPORTS = {
    "validation_findings.csv": "SELECT check_name, order_no, detail FROM v_validation_findings ORDER BY check_name, order_no",
    "validation_summary.csv": "SELECT check_name, findings FROM v_validation_summary",
    "interface_health_weekly.csv": "SELECT * FROM v_interface_health_weekly",
    "oee_weekly.csv": "SELECT * FROM v_oee_weekly",
}


def export_all(conn, target=EXPORT_DIR) -> list:
    target.mkdir(exist_ok=True)
    written = []
    for filename, query in EXPORTS.items():
        cur = conn.execute(query)
        path = target / filename
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([col.name for col in cur.description])
            writer.writerows(cur.fetchall())
        written.append(path)
    return written


def main() -> None:
    with db.connect() as conn:
        for path in export_all(conn):
            print(f"wrote {path.relative_to(db.ROOT)}")


if __name__ == "__main__":
    main()
