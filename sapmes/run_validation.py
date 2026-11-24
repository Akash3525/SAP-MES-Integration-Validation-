"""Run all seven validation checks and print a summary.

Usage:  python -m sapmes.run_validation [--details N]
"""
import argparse

from sapmes import db


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--details", type=int, default=3, metavar="N",
                        help="example findings to print per check (default 3)")
    args = parser.parse_args()

    with db.connect() as conn:
        as_of = conn.execute("SELECT as_of FROM validation_settings").fetchone()[0]
        summary = conn.execute("SELECT check_name, findings FROM v_validation_summary").fetchall()
        print(f"SAP PP <-> MES validation, data as of {as_of:%Y-%m-%d %H:%M %Z}\n")
        print(f"{'Check':<24}{'Findings':>8}")
        print("-" * 32)
        for check, n in summary:
            print(f"{check:<24}{n:>8}")
        print("-" * 32)
        print(f"{'Total':<24}{sum(n for _, n in summary):>8}")

        if args.details:
            for check, n in summary:
                if not n:
                    continue
                print(f"\n{check}:")
                rows = conn.execute(
                    "SELECT order_no, detail FROM v_validation_findings WHERE check_name = %s "
                    "ORDER BY order_no LIMIT %s", (check, args.details)).fetchall()
                for order_no, detail in rows:
                    print(f"  {order_no}  {detail}")
                if n > args.details:
                    print(f"  ... {n - args.details} more")


if __name__ == "__main__":
    main()
