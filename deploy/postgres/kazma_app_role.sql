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
--     create and change its own tables;
--   * makes kazma_space, the role a space's connection runs as once Spaces'
--     second lock is armed (S4-01): it may not sign in, owns nothing and is
--     a member of nothing; kazma_app holds it (may switch to it, inherits
--     nothing); it may use the rows of a space's table only where Kazma's
--     policy is already on that table, and nothing of the install's tables.
--     Nothing uses it until the lock is armed. To take it away again:
--     deploy/postgres/kazma_space_role_remove.sql.
-- The superuser itself is left as it is, for you to administer the server.
--
-- Run it as the superuser, in Kazma's database (the Docker setup):
--   docker exec -i kazma-db-win psql -U kazma -d kazma -v ON_ERROR_STOP=1 < deploy/postgres/kazma_app_role.sql
-- Then put the new address in .env, give the role its password read from
-- .env (never psql's \password prompt: a paste can arrive empty and clear
-- the password), check the sign-in with scripts/check_database_login.py, and
-- only then reload. docs/docs/ops/postgres-and-saas.md ("A database role that
-- is not a superuser") has every command. Gate: tests/test_pg_app_role.py.

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

-- The second lock's role (Spaces S4-01; kazma_core/db/row_security.py holds
-- the rules, puts the policies in place and gives these rights again after a
-- restore, which drops every grant). Gate: tests/test_row_security_rules.py.
DO $$
DECLARE
    r record;
    wrong boolean;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'kazma_space') THEN
        CREATE ROLE kazma_space NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
    ELSE
        ALTER ROLE kazma_space NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT;
    END IF;

    -- A member of nothing: were it a member of the tables' owner, it would
    -- count as the owner and pass every policy.
    FOR r IN
        SELECT b.rolname FROM pg_auth_members m JOIN pg_roles b ON b.oid = m.roleid
        WHERE m.member = (SELECT oid FROM pg_roles WHERE rolname = 'kazma_space')
    LOOP
        EXECUTE format('REVOKE %I FROM kazma_space', r.rolname);
    END LOOP;

    -- kazma_app holds it: it may switch to it, and inherits none of its rights
    -- (before PostgreSQL 16 a membership has no such options).
    IF current_setting('server_version_num')::int >= 160000 THEN
        EXECUTE 'SELECT EXISTS (SELECT 1 FROM pg_auth_members WHERE roleid = ''kazma_space''::regrole '
                'AND member = ''kazma_app''::regrole AND (inherit_option OR NOT set_option))' INTO wrong;
        IF wrong THEN
            EXECUTE 'REVOKE kazma_space FROM kazma_app';
        END IF;
        IF NOT pg_has_role('kazma_app', 'kazma_space', 'MEMBER') THEN
            EXECUTE 'GRANT kazma_space TO kazma_app WITH INHERIT FALSE, SET TRUE';
        END IF;
    ELSIF NOT pg_has_role('kazma_app', 'kazma_space', 'MEMBER') THEN
        EXECUTE 'GRANT kazma_space TO kazma_app';
    END IF;

    GRANT USAGE ON SCHEMA public TO kazma_space;

    -- The rights the rules name, table by table: a space's table only where
    -- Kazma's policy is on it (row security on), the one-row intake lock as
    -- it is, the install's tables none.
    FOR r IN SELECT g.t, g.kind, g.rights FROM (VALUES
        -- BEGIN rights (generated from kazma_core/db/row_security.RULES)
        ('checkpoints', 'space', 'SELECT, INSERT, UPDATE, DELETE'),
        ('checkpoint_blobs', 'space', 'SELECT, INSERT, UPDATE, DELETE'),
        ('checkpoint_writes', 'space', 'SELECT, INSERT, UPDATE, DELETE'),
        ('checkpoint_migrations', 'install', ''),
        ('kazma_settings', 'install', ''),
        ('kazma_chat_sessions', 'space', 'SELECT, INSERT, UPDATE, DELETE'),
        ('kazma_swarm_tasks', 'space', 'SELECT, INSERT, UPDATE, DELETE'),
        ('kazma_swarm_worker_metrics', 'install', ''),
        ('kazma_platform_users', 'install', ''),
        ('kazma_web_sessions', 'install', ''),
        ('document_jobs', 'space', 'SELECT, INSERT, UPDATE, DELETE'),
        ('document_job_events', 'space', 'SELECT, INSERT'),
        ('document_intake_reservations', 'space', 'SELECT'),
        ('document_intake_mutex', 'grant', 'SELECT, UPDATE'),
        ('documents', 'space', 'SELECT, INSERT, UPDATE, DELETE'),
        ('document_blobs', 'space', 'SELECT, INSERT, UPDATE'),
        ('document_versions', 'space', 'SELECT, INSERT, UPDATE, DELETE'),
        ('document_artifacts', 'space', 'SELECT, INSERT, UPDATE, DELETE'),
        ('document_acl', 'space', 'SELECT, INSERT, UPDATE, DELETE'),
        ('document_tombstones', 'space', 'SELECT, INSERT, UPDATE, DELETE'),
        ('document_chunks', 'space', 'SELECT, INSERT, UPDATE, DELETE'),
        ('document_audit_events', 'space', 'SELECT, INSERT'),
        ('kazma_episodes', 'space', 'SELECT, INSERT, UPDATE, DELETE'),
        ('kazma_beliefs', 'space', 'SELECT, INSERT, UPDATE, DELETE')
        -- END rights
    ) AS g(t, kind, rights)
    LOOP
        IF to_regclass(format('public.%I', r.t)) IS NULL THEN
            CONTINUE;
        END IF;
        EXECUTE format('REVOKE ALL ON public.%I FROM kazma_space', r.t);
        IF r.rights = '' THEN
            CONTINUE;
        END IF;
        IF r.kind = 'space' AND NOT EXISTS (
                SELECT 1 FROM pg_class c JOIN pg_policy p ON p.polrelid = c.oid
                WHERE c.oid = to_regclass(format('public.%I', r.t)) AND c.relrowsecurity
                  AND p.polname = 'kazma_space_rows') THEN
            CONTINUE;
        END IF;
        EXECUTE format('GRANT %s ON public.%I TO kazma_space', r.rights, r.t);
    END LOOP;

    -- The sequences of the tables it may insert into.
    FOR r IN
        SELECT s.oid::regclass AS seq FROM pg_class s
        JOIN pg_depend d ON d.objid = s.oid AND d.classid = 'pg_class'::regclass AND d.deptype IN ('a', 'i')
        JOIN pg_class t ON t.oid = d.refobjid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE s.relkind = 'S' AND n.nspname = 'public'
          AND has_table_privilege('kazma_space', t.oid, 'INSERT')
    LOOP
        EXECUTE format('GRANT USAGE ON SEQUENCE %s TO kazma_space', r.seq);
    END LOOP;
END
$$;

COMMIT;
