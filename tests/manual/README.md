# Manual diagnostics

Operator-run scripts that require a running API or live provider credentials.
They are retained for troubleshooting but are not automated tests and are not
part of pytest collection.

These scripts predate the current versioned API surface and may contain stale
endpoint assumptions. Verify the target route before use. They are candidates
for modernization or retirement, but must not be deleted without owner
approval.
