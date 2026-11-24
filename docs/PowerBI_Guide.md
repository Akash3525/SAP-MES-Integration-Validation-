# Power BI Guide

How to build the dashboard from the four CSV files in `exports/`.
This repository has no `.pbix` file. Build it in Power BI Desktop with these steps, then add screenshots to the README.

## 1. Load the data

**Get Data → Text/CSV** and load each file:

| File | Table name | Grain |
|---|---|---|
| `validation_findings.csv` | Findings | one row per finding |
| `validation_summary.csv` | Summary | one row per check |
| `interface_health_weekly.csv` | InterfaceHealth | week × direction × message type |
| `oee_weekly.csv` | OEE | week × line |

In Power Query, set `week_start` to **Date** and the rate columns to **Decimal Number**.

Instead of the CSVs, you can connect live with **Get Data → PostgreSQL database** and load the views
`v_validation_findings`, `v_validation_summary`, `v_interface_health_weekly` and `v_oee_weekly`.

## 2. Measures (DAX)

```DAX
Total Findings = SUM ( Summary[findings] )

Orders With Findings = DISTINCTCOUNT ( Findings[order_no] )

Message Attempts = SUM ( InterfaceHealth[attempts] )

Interface Success Rate =
DIVIDE ( SUM ( InterfaceHealth[processed] ), [Message Attempts] )

Interface Errors = SUM ( InterfaceHealth[errors] )

-- Weighted by units produced. Averaging the weekly OEE column would weight small weeks too heavily.
OEE % =
DIVIDE (
    SUMX ( OEE, OEE[oee] * ( OEE[good_qty] + OEE[scrap_qty] ) ),
    SUM ( OEE[good_qty] ) + SUM ( OEE[scrap_qty] )
)

Availability % = AVERAGE ( OEE[availability] )
Performance % = AVERAGE ( OEE[performance] )
Quality % = AVERAGE ( OEE[quality] )
```

Format the percentage measures as **Percentage** with one decimal place.

## 3. Suggested layout

**Page 1: Data Quality**
- Cards: Total Findings, Orders With Findings.
- Bar chart: `Summary[check_name]` by `findings`, sorted descending.
- Table: Findings (check_name, order_no, detail) with a slicer on check_name.

**Page 2: Interface Health**
- Line chart: `week_start` by Interface Success Rate, with `msg_type` as the legend.
- Stacked column: `week_start` by processed, errors and pending.
- Slicer: direction.

**Page 3: OEE**
- Cards: OEE %, Availability %, Performance %, Quality %.
- Line chart: `week_start` by `oee`, with `line` as the legend.
- Matrix: line as rows, week_start as columns, oee as values, with conditional formatting.

## 4. Screenshots for the README

Save one screenshot per page as `docs/img/powerbi_*.png` and link them in the README.
