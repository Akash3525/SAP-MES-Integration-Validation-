# Worked example: a stuck goods-issue message

An end-to-end investigation of one `stuck_messages` finding, run against the project's simulated data. Every query result below is real output from this repository's database. The fix is simulated and was run inside a transaction that was rolled back, so the demo data is unchanged.

> This is a simulated case for learning, not a real SAP production incident. The tables are simplified stand-ins for SAP and MES objects.

## 1. The finding

`python -m sapmes.run_validation` lists order **1000000011** under `stuck_messages`:

```sql
SELECT check_name, order_no, detail FROM v_validation_findings WHERE order_no = '1000000011';
```
```
check_name     | order_no   | detail
stuck_messages | 1000000011 | MBGMCR MES_TO_SAP ERROR, retries 3, last attempt 2026-09-27 16:46: Order is locked for confirmation (status TECO)
```

The goods-issue message (MBGMCR) from MES to SAP has failed and run out of retries.

## 2. Message history

```sql
SELECT msg_id, msg_type, direction, status, retry_count, error_text, last_attempt_at
FROM interface_messages WHERE order_no = '1000000011' ORDER BY msg_id;
```
```
msg_id | msg_type | direction  | status    | retry | error_text                                         | last_attempt (UTC)
16     | LOIPRO   | SAP_TO_MES | PROCESSED | 0     |                                                    | 2026-09-26 15:15
36     | CONF     | MES_TO_SAP | PROCESSED | 0     |                                                    | 2026-09-27 15:55
37     | MBGMCR   | MES_TO_SAP | ERROR     | 0     | HTTP 503 Service Unavailable                       | 2026-09-27 15:55
38     | MBGMCR   | MES_TO_SAP | ERROR     | 1     | Connection timeout to receiving system after 30 s  | 2026-09-27 16:23
39     | MBGMCR   | MES_TO_SAP | ERROR     | 2     | HTTP 503 Service Unavailable                       | 2026-09-27 16:38
40     | MBGMCR   | MES_TO_SAP | ERROR     | 3     | Order is locked for confirmation (status TECO)     | 2026-09-27 16:46
```

**Reading it:** the order download and the confirmation went through. The goods issue first hit two transient errors (503, timeout). By the time the receiving system was back, the last retry failed with a **business error**: the order had already been technically completed (TECO). More retries will not fix a business error.

## 3. Compare SAP and MES

```sql
SELECT order_no, material_no, planned_qty, sap_status FROM sap_production_orders WHERE order_no = '1000000011';
SELECT yield_qty, scrap_qty, confirmed_at FROM sap_confirmations WHERE order_no = '1000000011';
SELECT m.mes_status, p.good_qty, p.scrap_qty, p.end_ts
FROM mes_work_orders m JOIN mes_production_records p USING (order_no) WHERE m.order_no = '1000000011';
SELECT component_material, consumed_qty FROM mes_material_consumption WHERE order_no = '1000000011';
```
```
SAP order        : FG-100010, planned 91,000, status TECO
SAP confirmation : yield 88,210, scrap 1,632, confirmed 2026-09-27 16:04
MES              : COMPLETE, good 88,210, scrap 1,632, finished 2026-09-27 15:55
MES consumption  : API-200010 44.379 kg · EXC-300010 10.821 kg · EXC-300020 2.731 kg  (not yet in SAP)
```

Quantities agree, and both systems show the order as finished. **Only the component consumption is missing in SAP**, because the goods issue never posted.

## 4. Root cause

The order was set to TECO in SAP while the goods-issue message was still retrying after the outage. A TECO order blocks further postings, so the final retry was rejected.

## 5. Correction (simulated)

In a real system: revoke technical completion on the order (CO02), reprocess the failed IDoc (BD87), check the goods movement in MIGO, then set TECO again.
In this simulation, the reprocessed message is a new successful attempt:

```
msg_id | msg_type | status    | retry_count | last_attempt (UTC)
1105   | MBGMCR   | PROCESSED | 4           | 2026-11-20 17:50
```

## 6. Re-run the check

```sql
SELECT count(*) AS still_flagged FROM v_chk_stuck_messages WHERE order_no = '1000000011';
SELECT findings FROM v_validation_summary WHERE check_name = 'stuck_messages';
```
```
still_flagged = 0
stuck_messages findings: 9 → 8
```

## 7. Result and prevention

The order no longer appears in any check, and SAP now holds the component consumption.

To prevent a repeat:
- Don't set TECO while the order still has failed or pending interface messages. Run `stuck_messages` before closing orders.
- Alert on business errors, such as a locked order or a closed period, on the first failure. Only transient errors such as 503s and timeouts benefit from automatic retries.

See the [Troubleshooting Playbook](Troubleshooting_Playbook.md) for the other six checks.
