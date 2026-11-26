-- Take Spaces' second lock out of Kazma's database for good (S4-01).
--
-- What it does, safe to run again:
--   * drops Kazma's policy (kazma_space_rows) from every table that has it
--     and turns row security off there;
--   * revokes every right kazma_space holds in this database;
--   * then drops the role kazma_space.
-- The checkpoint tables' space_id column stays (no statement of an older
-- Kazma names it), and so does the function kazma_space_id() (every restore
-- makes it; with no policy nothing calls it).
--
-- Only once Kazma no longer arms the lock (a build without it, or Spaces
-- off) -- otherwise a start that arms it puts the rules back. Kazma signs in
-- as the tables' owner, which policies never held back: nothing it reads or
-- writes changes.
--
-- Run it as the superuser, in Kazma's database:
--   psql -U postgres -d kazma -v ON_ERROR_STOP=1 -f deploy/postgres/kazma_space_role_remove.sql
-- The role is the server's, not the database's: if another database still
-- holds a right of kazma_space, the last statement says which, and the
-- policies are gone already. Gate: tests/test_pg_row_security.py.

BEGIN;
-- A table another session holds stops the script here instead of waiting
-- behind it; run it again when Kazma is idle.
SET LOCAL lock_timeout = '10s';

DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT n.nspname, c.relname FROM pg_policy p
        JOIN pg_class c ON c.oid = p.polrelid JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE p.polname = 'kazma_space_rows'
    LOOP
        EXECUTE format('DROP POLICY kazma_space_rows ON %I.%I', r.nspname, r.relname);
        EXECUTE format('ALTER TABLE %I.%I DISABLE ROW LEVEL SECURITY', r.nspname, r.relname);
    END LOOP;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kazma_space') THEN
        EXECUTE 'DROP OWNED BY kazma_space';
    END IF;
END
$$;

COMMIT;

DROP ROLE IF EXISTS kazma_space;
