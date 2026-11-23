# Database migration rules

BE1 owns the versioned migration runner for shared schema changes.

- `schema.sql` is the idempotent initial baseline (migration version 1).
- Later changes go in sequential files named `NNNN_short_description.sql`.
- Never rewrite a migration already used by another developer. Add a new version.
- The runner applies each migration once and records it in `schema_migrations`.
- Existing databases without migration history receive the idempotent baseline,
  which creates missing objects without dropping existing records, then receive
  pending numbered migrations.
- Keep BE2 tables and business rules owned by BE2. A shared change to those
  tables needs coordination with BE2 before adding it to a migration.
- Use `python -m backend.seed_dev` for BE2 records and
  `python -m backend.seed_sales_dev` for BE1 records when building a local demo DB.

Inspect applied versions with `SELECT version, name, applied_at FROM schema_migrations ORDER BY version;`.
