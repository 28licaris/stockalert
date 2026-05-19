# Athena — DDL is Hive (backticks); DML is Trino (double quotes)

Athena engine v3 parses different statement types with different SQL
dialects:

- **DDL** (`CREATE EXTERNAL TABLE`, `ALTER`, `DROP`) → Hive syntax →
  escape reserved-word identifiers with `` `backticks` ``.
- **DML** (`SELECT`, `INSERT`, `DELETE`, `MERGE`, `OPTIMIZE`) → Trino
  syntax → escape with `"double quotes"`.

These are **not interchangeable**.

- `SELECT \`timestamp\` ...` errors with
  *"backquoted identifiers are not supported; use double quotes."*
- `CREATE EXTERNAL TABLE x ("timestamp" timestamp)` errors with
  *"mismatched input 'EXTERNAL'"* because Trino does not have that DDL.

This was broken twice in one session — once by mass-replacing backticks
across a file (broke the DDL), then by leaving them in (broke the DML).

## How to apply

When writing Athena SQL in a Python boto3 script, mentally tag each
statement as DDL or DML before choosing the quoting style. `timestamp`
and `date` are the columns most commonly hit — both reserved.

## Concrete example

From `scripts/bronze_import_athena.py`:

```python
# DDL — backticks
"""CREATE EXTERNAL TABLE x (
     `timestamp` timestamp,
     symbol      string
   )"""

# DML — double quotes
"""INSERT INTO target
   SELECT "timestamp", symbol FROM x"""
```

## Related

- [`bronze_idempotency.md`](bronze_idempotency.md) — Athena is the
  right tool for bronze maintenance (DELETE, OPTIMIZE) because it
  skips the PyIceberg slow path.
- [`../coding.md`](../coding.md) rule 5 — verify cross-side after any
  write, including Athena DML.
