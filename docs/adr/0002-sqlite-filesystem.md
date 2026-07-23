# ADR-0002: SQLite and Filesystem Persistence

**Status:** Accepted

Store queryable experiment metadata in SQLite through SQLAlchemy and immutable large evidence in a SHA-256-addressed filesystem store. Alembic migrations own the schema. PostgreSQL and object storage are deferred until concurrent writers or remote retention make local storage insufficient.
