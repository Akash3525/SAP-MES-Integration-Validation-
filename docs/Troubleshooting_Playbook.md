# Troubleshooting Playbook

What to do when a validation check reports a finding. Each section lists the
likely causes, the queries to run, and how to close the finding.

The steps describe a typical SAP PP to MES interface. The tables in this
project are simplified stand-ins, and the real SAP transaction codes are given
for reference only.

---

## 1. `missing_in_mes`: released in SAP, not in MES

**Signal:** the order was created longer ago than the grace period (default 24 h) and has no MES work order.

**Likely causes**
- The order download (LOIPRO IDoc or API call) failed, or shows as processed but was dropped by middleware.
- MES rejected the order because of master data, such as an unknown material or a missing routing.
- The order was released in a plant or production version that is not interfaced.

**Investigate**
```sql
SELECT * FROM interface_messages WHERE order_no = :order_no ORDER BY msg_id;
SELECT * FROM sap_production_orders WHERE order_no = :order_no;
```
In a real system, also check WE02/WE05 (IDoc monitor), the middleware logs and the MES inbound queue.

**Resolve:** fix the root cause, then resend the order (BD87 or the equivalent API replay). Confirm it appears in MES exactly once, because a careless resend causes `duplicate_mes`.

---

## 2. `no_sap_confirmation`: complete in MES, no confirmation in SAP

**Signal:** MES status is COMPLETE with a production record, but SAP has no confirmation.

**Likely causes**
- The confirmation message was never triggered, for example because the operator closed the order without the final step.
- The message failed and was purged, or never left the MES outbox.

**Investigate**
```sql
SELECT * FROM mes_production_records WHERE order_no = :order_no;
SELECT * FROM interface_messages WHERE order_no = :order_no AND msg_type = 'CONF';
```

**Resolve:** resend the confirmation from MES. Post manually in SAP (CO11N) only when MES cannot resend, and record why. Then check that `qty_mismatch` is clean for the order.

---

## 3. `duplicate_mes`: order exists more than once in MES

**Signal:** more than one MES work order with the same SAP order number.

**Likely causes**
- The IDoc was reprocessed manually after a timeout, although the first attempt had already succeeded.
- The MES inbound interface is not idempotent, so it does not ignore an order number it already has.

**Investigate**
```sql
SELECT * FROM mes_work_orders WHERE order_no = :order_no ORDER BY mes_id;
SELECT * FROM interface_messages WHERE order_no = :order_no AND msg_type = 'LOIPRO';
```

**Resolve:** keep the work order that has execution data and cancel the other one in MES. Raise a change request to make the inbound interface reject duplicate order numbers.

---

## 4. `qty_mismatch`: MES and SAP quantities differ

**Signal:** MES good or scrap quantity differs from the SAP confirmed yield or scrap by more than the tolerance.

**Likely causes**
- A partial confirmation was posted twice, or a reversal was not interfaced.
- The quantity was corrected in MES after the confirmation was sent.
- The two systems use different units of measure or conversion factors.

**Investigate**
```sql
SELECT * FROM mes_production_records WHERE order_no = :order_no;
SELECT * FROM sap_confirmations     WHERE order_no = :order_no;
```

**Resolve:** agree with production which number is correct. Correct SAP by cancelling and re-posting the confirmation (CO13 then CO11N), or correct MES with an audited change. Never edit only one side.

---

## 5. `consumption_mismatch`: component usage outside tolerance

**Signal:** at least one component was consumed more than 5 % away from the BOM quantity, scaled to the quantity produced.

**Likely causes**
- A wrong lot or quantity was scanned at dispensing.
- The BOM or recipe was changed in one system only.
- Real process loss above standard, which should be a deviation and not a data fix.

**Investigate**
```sql
SELECT * FROM mes_material_consumption WHERE order_no = :order_no;
SELECT * FROM sap_order_components     WHERE order_no = :order_no;
```

**Resolve:** if the data is wrong, correct the goods movement (MIGO reversal and re-post). If the usage is real, raise a deviation and leave the numbers as they are.

---

## 6. `status_mismatch`: status differs between SAP and MES

**Signal:** SAP shows CNF or TECO while MES is not COMPLETE, or SAP is still REL although it has a confirmation and MES is COMPLETE.

**Likely causes**
- The order was closed in one system only.
- The status update was part of a message that failed.

**Investigate**
```sql
SELECT order_no, sap_status FROM sap_production_orders WHERE order_no = :order_no;
SELECT order_no, mes_status FROM mes_work_orders       WHERE order_no = :order_no;
```

**Resolve:** follow the order's real physical state. Close the order in the lagging system, or reopen it in the leading system if it was closed too early.

---

## 7. `stuck_messages`: interface message pending or out of retries

**Signal:** the latest attempt of a message has been PENDING for longer than 60 minutes, or is ERROR with 3 or more retries.
Earlier failed attempts that were later processed are not reported.

**Likely causes**
- A business error that retries cannot fix, such as missing stock, a closed posting period or an order locked by another user.
- The receiving system or middleware is down, so messages stay pending.

**Investigate**
```sql
SELECT msg_id, msg_type, status, retry_count, error_text, last_attempt_at
FROM interface_messages WHERE order_no = :order_no ORDER BY msg_id;
```

**Resolve:** fix the business cause first, for example by posting the stock transfer or opening the period, and then reprocess the message. If many messages are pending at once, treat it as an outage, not as individual findings.

---

## Daily routine

1. Run `python -m sapmes.run_validation`.
2. Work the checks in this order: stuck messages, missing in MES, no confirmation, then the rest. Interface failures cause most of the other findings.
3. Re-run until the summary is zero or every remaining finding has an owner.
