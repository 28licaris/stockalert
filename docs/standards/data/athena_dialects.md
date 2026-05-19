# Athena — DDL is Hive, DML is Trino

- **DDL** (`CREATE EXTERNAL TABLE`, `ALTER`, `DROP`) → Hive →
  `` `backticks` ``
- **DML** (`SELECT`, `INSERT`, `DELETE`, `MERGE`, `OPTIMIZE`) → Trino →
  `"double quotes"`

Not interchangeable. Errors:

```
SELECT `timestamp` …                       backquoted identifiers not supported
CREATE EXTERNAL TABLE x ("timestamp" …)    mismatched input 'EXTERNAL'
```

Most-hit reserved columns: `timestamp`, `date`.

## Example

```python
# DDL — backticks
"CREATE EXTERNAL TABLE x (`timestamp` timestamp, symbol string)"

# DML — double quotes
'INSERT INTO target SELECT "timestamp", symbol FROM x'
```

Pattern source: `scripts/bronze_import_athena.py`.
