# RB-08 Audit chain broken

**Signal.** "Check the chain" in the admin console reports a break, or `/api/admin/audit/verify` returns `valid: false`.

Treat as a security incident: rows were modified outside the application (append-only triggers make this require a privileged database role).

1. Freeze: revoke interactive DB access; snapshot the database volume.
2. Compare the broken row with backups/WAL to determine what changed and by whom (`log_statement=ddl`, connection logs).
3. Do not repair the chain in place; record the finding and restore from a verified backup if required.
