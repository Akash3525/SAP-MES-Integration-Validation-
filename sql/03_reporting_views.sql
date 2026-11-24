-- Reporting views used for the CSV exports and the Power BI dashboard.

-- Interface health per ISO week, direction and message type.
-- Every row in interface_messages is one attempt, so retries count as traffic.
CREATE VIEW v_interface_health_weekly AS
SELECT to_char(date_trunc('week', created_at), 'YYYY-MM-DD') AS week_start,
       direction,
       msg_type,
       count(*)                                          AS attempts,
       count(*) FILTER (WHERE status = 'PROCESSED')      AS processed,
       count(*) FILTER (WHERE status = 'ERROR')          AS errors,
       count(*) FILTER (WHERE status = 'PENDING')        AS pending,
       round(count(*) FILTER (WHERE status = 'PROCESSED')::numeric / count(*), 4) AS success_rate
FROM interface_messages
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;

-- OEE per line and ISO week, from MES production records.
--   Availability = actual run time / planned run time
--   Performance  = ideal cycle time x total count / actual run time
--   Quality      = good count / total count
CREATE VIEW v_oee_weekly AS
WITH agg AS (
    SELECT line,
           to_char(date_trunc('week', start_ts), 'YYYY-MM-DD') AS week_start,
           sum(planned_runtime_min)                         AS planned_min,
           sum(actual_runtime_min)                          AS run_min,
           sum(ideal_cycle_time_sec * (good_qty + scrap_qty)) / 60.0 AS ideal_min,
           sum(good_qty)                                    AS good,
           sum(good_qty + scrap_qty)                        AS total
    FROM mes_production_records
    GROUP BY 1, 2
)
SELECT line, week_start,
       round(run_min / planned_min, 4)                           AS availability,
       round(ideal_min / run_min, 4)                             AS performance,
       round(good / total, 4)                                    AS quality,
       round((run_min / planned_min) * (ideal_min / run_min) * (good / total), 4) AS oee,
       good AS good_qty,
       total - good AS scrap_qty
FROM agg
ORDER BY week_start, line;
