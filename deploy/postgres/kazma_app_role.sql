-- Give Kazma a database role that is not a superuser (functional audit
-- 2026-11, FA-218). A superuser can read and change every database on the
-- server and run programs on the database host; Kazma needs none of that.
--
-- What it does, in one transaction, safe to run again:
--   * creates the login role kazma_app (not a superuser, may not create
--     roles; may create databases, which the weekly restore rehearsal needs
--     for its scratch copy -- drop CREATEDB and turn the rehearsal off if you
--     prefer: backups.pg.restore_rehearsal = false);
--   * makes kazma_app the owner of this database, its public schema and
--     every table, sequence, view and function in it, so Kazma can still
--     create and change its own tables.
-- The superuser itself is left as it is, for you to administer the server.
--
-- Run it as the superuser, in Kazma's database (the Docker setup):
--   docker exec -i kazma-db-win psql -U kazma -d kazma -v ON_ERROR_STOP=1 < deploy/postgres/kazma_app_role.sql
-- Then give the role a password, typed at the prompt (it stays off command
-- lines and files):
--   docker exec -it kazma-db-win psql -U kazma -d kazma -c "\password kazma_app"
-- Then point Kazma at it: KAZMA_DATABASE_URL in .env, and Settings -> Memory
-- -> Memory backends -> Postgres DSN when the memory mirror is on.
-- docs/docs/ops/postgres-and-saas.md ("A database role that is not a
-- superuser") has the whole procedure. Gate: tests/test_pg_app_role.py.

BEGIN;
-- A table another session holds stops the script here instead of waiting
-- behind it; run it again when Kazma is idle.
SET LOCAL lock_timeout = '10s';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kazma_app') THEN
        CREATE ROLE kazma_app LOGIN NOSUPERUSER NOCREATEROLE CREATEDB NOREPLICATION NOBYPASSRLS;
    ELSE
        ALTER ROLE kazma_app LOGIN NOSUPERUSER NOCREATEROLE CREATEDB NOREPLICATION NOBYPASSRLS;
    END IF;
END
$$;

DO $$
DECLARE
    r record;
BEGIN
    EXECUTE format('ALTER DATABASE %I OWNER TO kazma_app', current_database());
    ALTER SCHEMA public OWNER TO kazma_app;

    -- Tables (their indexes and the sequences their columns own follow them).
    FOR r IN
        SELECT c.oid::regclass AS obj, c.relkind
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
    LOOP
        EXECUTE format('ALTER %s %s OWNER TO kazma_app',
                       CASE r.relkind
                           WHEN 'v' THEN 'VIEW'
                           WHEN 'm' THEN 'MATERIALIZED VIEW'
                           WHEN 'f' THEN 'FOREIGN TABLE'
                           ELSE 'TABLE'
                       END,
                       r.obj);
    END LOOP;

    -- Sequences no column owns (one a column owns moved with its table, and
    -- Postgres refuses to move it on its own).
    FOR r IN
        SELECT c.oid::regclass AS obj
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'S'
          AND NOT EXISTS (
              SELECT 1 FROM pg_depend d
              WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype IN ('a', 'i'))
    LOOP
        EXECUTE format('ALTER SEQUENCE %s OWNER TO kazma_app', r.obj);
    END LOOP;

    -- Functions (the document audit guard's trigger function, when present).
    FOR r IN
        SELECT p.oid::regprocedure AS fn
        FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
    LOOP
        EXECUTE format('ALTER ROUTINE %s OWNER TO kazma_app', r.fn);
    END LOOP;

    -- Enum types and stand-alone composite types.
    FOR r IN
        SELECT t.oid::regtype AS ty
        FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public'
          AND (t.typtype = 'e'
               OR (t.typtype = 'c' AND (SELECT relkind FROM pg_class WHERE oid = t.typrelid) = 'c'))
    LOOP
        EXECUTE format('ALTER TYPE %s OWNER TO kazma_app', r.ty);
    END LOOP;
END
$$;

COMMIT;
