-- Seven reconciliation checks between SAP PP and MES.
-- Each view returns one row per finding with the order number and a short,
-- human-readable detail. Thresholds come from validation_settings.

-- 1. Released in SAP for longer than the grace period, but never arrived in MES.
CREATE VIEW v_chk_missing_in_mes AS
SELECT o.order_no,
       format('Created %s, status %s, not in MES after %s h',
              to_char(o.created_at, 'YYYY-MM-DD HH24:MI'), o.sap_status, s.mes_grace_hours) AS detail
FROM sap_production_orders o
CROSS JOIN validation_settings s
WHERE o.created_at < s.as_of - make_interval(hours => s.mes_grace_hours)
  AND NOT EXISTS (SELECT 1 FROM mes_work_orders m WHERE m.order_no = o.order_no);

-- 2. Finished in MES, but SAP never received a confirmation.
CREATE VIEW v_chk_no_sap_confirmation AS
SELECT DISTINCT m.order_no,
       format('MES complete since %s, no SAP confirmation',
              to_char(p.end_ts, 'YYYY-MM-DD HH24:MI')) AS detail
FROM mes_work_orders m
JOIN mes_production_records p ON p.order_no = m.order_no
WHERE m.mes_status = 'COMPLETE'
  AND NOT EXISTS (SELECT 1 FROM sap_confirmations c WHERE c.order_no = m.order_no);

-- 3. Same SAP order downloaded into MES more than once.
CREATE VIEW v_chk_duplicate_mes AS
SELECT order_no,
       format('%s MES work orders (ids %s)', count(*), string_agg(mes_id::text, ', ' ORDER BY mes_id)) AS detail
FROM mes_work_orders
GROUP BY order_no
HAVING count(*) > 1;

-- 4. Quantity reported by MES differs from the quantity confirmed in SAP.
CREATE VIEW v_chk_qty_mismatch AS
WITH mes AS (
    SELECT order_no, sum(good_qty) AS good, sum(scrap_qty) AS scrap
    FROM mes_production_records GROUP BY order_no
), sap AS (
    SELECT order_no, sum(yield_qty) AS good, sum(scrap_qty) AS scrap
    FROM sap_confirmations GROUP BY order_no
)
SELECT mes.order_no,
       format('MES good/scrap %s/%s vs SAP %s/%s', mes.good, mes.scrap, sap.good, sap.scrap) AS detail
FROM mes
JOIN sap USING (order_no)
CROSS JOIN validation_settings s
WHERE abs(mes.good - sap.good) > s.qty_tolerance
   OR abs(mes.scrap - sap.scrap) > s.qty_tolerance;

-- 5. Component consumption in MES outside tolerance of the BOM requirement,
--    scaled to the quantity actually produced.
CREATE VIEW v_chk_consumption_mismatch AS
WITH produced AS (
    SELECT order_no, sum(good_qty + scrap_qty) AS qty
    FROM mes_production_records GROUP BY order_no
), comparison AS (
    SELECT c.order_no, c.component_material, c.consumed_qty,
           round(r.required_qty * p.qty / o.planned_qty, 3) AS expected_qty
    FROM mes_material_consumption c
    JOIN sap_order_components r USING (order_no, component_material)
    JOIN sap_production_orders o USING (order_no)
    JOIN produced p USING (order_no)
)
SELECT DISTINCT ON (order_no) order_no,
       format('%s consumed %s, expected %s (%s%%)', component_material, consumed_qty, expected_qty,
              round(100 * (consumed_qty - expected_qty) / expected_qty, 1)) AS detail
FROM comparison
CROSS JOIN validation_settings s
WHERE abs(consumed_qty - expected_qty) / expected_qty > s.consumption_tolerance
ORDER BY order_no, abs(consumed_qty - expected_qty) / expected_qty DESC;

-- 6. Order status disagrees between the two systems.
CREATE VIEW v_chk_status_mismatch AS
WITH mes AS (
    SELECT DISTINCT order_no, mes_status FROM mes_work_orders
)
SELECT o.order_no,
       format('SAP %s vs MES %s', o.sap_status, mes.mes_status) AS detail
FROM sap_production_orders o
JOIN mes USING (order_no)
WHERE (o.sap_status IN ('CNF', 'TECO') AND mes.mes_status <> 'COMPLETE')
   OR (o.sap_status = 'REL' AND mes.mes_status = 'COMPLETE'
       AND EXISTS (SELECT 1 FROM sap_confirmations c WHERE c.order_no = o.order_no));

-- 7. Latest attempt of an interface message is stuck: pending too long, or
--    failed and out of retries. Earlier failed attempts that were later
--    processed are not findings.
CREATE VIEW v_chk_stuck_messages AS
WITH latest AS (
    SELECT DISTINCT ON (order_no, direction, msg_type) *
    FROM interface_messages
    ORDER BY order_no, direction, msg_type, msg_id DESC
)
SELECT l.order_no,
       format('%s %s %s, retries %s, last attempt %s%s', l.msg_type, l.direction, l.status, l.retry_count,
              to_char(l.last_attempt_at, 'YYYY-MM-DD HH24:MI'),
              coalesce(': ' || l.error_text, '')) AS detail
FROM latest l
CROSS JOIN validation_settings s
WHERE (l.status = 'PENDING' AND l.last_attempt_at < s.as_of - make_interval(mins => s.stuck_after_minutes))
   OR (l.status = 'ERROR' AND l.retry_count >= s.max_retries);

---------------------------------------------------------------- Combined
CREATE VIEW v_validation_findings AS
          SELECT 'missing_in_mes'       AS check_name, order_no, detail FROM v_chk_missing_in_mes
UNION ALL SELECT 'no_sap_confirmation',                order_no, detail FROM v_chk_no_sap_confirmation
UNION ALL SELECT 'duplicate_mes',                      order_no, detail FROM v_chk_duplicate_mes
UNION ALL SELECT 'qty_mismatch',                       order_no, detail FROM v_chk_qty_mismatch
UNION ALL SELECT 'consumption_mismatch',               order_no, detail FROM v_chk_consumption_mismatch
UNION ALL SELECT 'status_mismatch',                    order_no, detail FROM v_chk_status_mismatch
UNION ALL SELECT 'stuck_messages',                     order_no, detail FROM v_chk_stuck_messages;

CREATE VIEW v_validation_summary AS
SELECT c.check_name, count(f.order_no) AS findings
FROM (VALUES ('missing_in_mes', 1), ('no_sap_confirmation', 2), ('duplicate_mes', 3),
             ('qty_mismatch', 4), ('consumption_mismatch', 5), ('status_mismatch', 6),
             ('stuck_messages', 7)) AS c(check_name, sort)
LEFT JOIN v_validation_findings f USING (check_name)
GROUP BY c.check_name, c.sort
ORDER BY c.sort;
