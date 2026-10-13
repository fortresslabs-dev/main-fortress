-- ============================================================================
-- Fortress P2 — Business Analyst read-only role bootstrap
-- ============================================================================
-- Run once per bank, after pipeline2-data.yml deploys and after the BA
-- read-only password has been set in Secrets Manager
-- (fortress/{BankId}/db-password-readonly).
--
-- Idempotent. Safe to re-run.
--
-- Invocation (from start_ba_session.sh OR an engineer's psql shell as the
-- master user):
--
--   BA_PASSWORD=$(aws secretsmanager get-secret-value \
--                   --secret-id fortress/${BANK_ID}/db-password-readonly \
--                   --query SecretString --output text)
--
--   psql "host=$DB_HOST user=fortress_$BANK_ID dbname=fortress_p2" \
--     -v ba_password="$BA_PASSWORD" \
--     -f init_ba_readonly_role.sql
--
-- The role created here has SELECT only. INSERT/UPDATE/DELETE/DDL are
-- refused at the Postgres level, even if a BA tries them at the psql prompt.
-- ============================================================================

\set ON_ERROR_STOP on
\set ba_role 'fortress_ba_ro'

BEGIN;

-- ---------------------------------------------------------------------------
-- 1. Role: create if missing, refresh password every run from the secret.
--    NOINHERIT + NOCREATEDB + NOCREATEROLE + NOSUPERUSER are Postgres
--    defaults; spelled out here for examiner traceability.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fortress_ba_ro') THEN
    EXECUTE format(
      'CREATE ROLE fortress_ba_ro LOGIN PASSWORD %L '
      'NOINHERIT NOCREATEDB NOCREATEROLE NOSUPERUSER NOREPLICATION '
      'CONNECTION LIMIT 4 VALID UNTIL ''infinity''',
      :'ba_password'
    );
  ELSE
    EXECUTE format(
      'ALTER ROLE fortress_ba_ro WITH PASSWORD %L '
      'NOINHERIT NOCREATEDB NOCREATEROLE NOSUPERUSER NOREPLICATION '
      'CONNECTION LIMIT 4',
      :'ba_password'
    );
  END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- 2. CONNECT to the database, USAGE on schema. Without USAGE on schema
--    public the BA's SELECT grants below would silently 0-row.
-- ---------------------------------------------------------------------------
GRANT CONNECT ON DATABASE fortress_p2 TO fortress_ba_ro;
GRANT USAGE   ON SCHEMA   public      TO fortress_ba_ro;

-- ---------------------------------------------------------------------------
-- 3. SELECT on every existing table + sequence in public.
--    Sequences are needed for any SELECT that touches a SERIAL column's
--    currval()/nextval() through a view — defensive grant.
-- ---------------------------------------------------------------------------
GRANT SELECT ON ALL TABLES    IN SCHEMA public TO fortress_ba_ro;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO fortress_ba_ro;

-- ---------------------------------------------------------------------------
-- 4. Default privileges for any future table/sequence created by the
--    master user (i.e. every alembic migration from here forward).
--    Without this block, the BA would lose visibility on tables added by
--    migrations 024+.
--    FOR ROLE clause MUST be the role that owns objects created by alembic
--    — fortress_{BankId} per pipeline2-data.yml:183.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  master_role text;
BEGIN
  SELECT rolname INTO master_role
  FROM   pg_roles
  WHERE  rolname LIKE 'fortress\_%' ESCAPE '\'
    AND  rolname <> 'fortress_ba_ro'
    AND  rolsuper IS FALSE
    AND  rolcanlogin
  ORDER  BY rolname
  LIMIT  1;

  IF master_role IS NULL THEN
    RAISE EXCEPTION
      'Cannot find fortress master role (fortress_<bank>); aborting BA bootstrap';
  END IF;

  EXECUTE format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
    'GRANT SELECT ON TABLES TO fortress_ba_ro',
    master_role
  );
  EXECUTE format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
    'GRANT SELECT ON SEQUENCES TO fortress_ba_ro',
    master_role
  );
END
$$;

-- ---------------------------------------------------------------------------
-- 5. Hard deny on any future-table write privilege that may slip through
--    a careless GRANT. Belt-and-suspenders; cheap to keep.
-- ---------------------------------------------------------------------------
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
  ON ALL TABLES IN SCHEMA public FROM fortress_ba_ro;
REVOKE USAGE, UPDATE
  ON ALL SEQUENCES IN SCHEMA public FROM fortress_ba_ro;

-- Same denial for future objects (mirrors block 4).
DO $$
DECLARE
  master_role text;
BEGIN
  SELECT rolname INTO master_role
  FROM   pg_roles
  WHERE  rolname LIKE 'fortress\_%' ESCAPE '\'
    AND  rolname <> 'fortress_ba_ro'
    AND  rolsuper IS FALSE
    AND  rolcanlogin
  ORDER  BY rolname
  LIMIT  1;

  EXECUTE format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
    'REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER '
    'ON TABLES FROM fortress_ba_ro',
    master_role
  );
END
$$;

COMMIT;

-- ---------------------------------------------------------------------------
-- 6. Smoke test — must return one row, must error on any write attempt.
--    Run interactively after applying:
--
--     SET ROLE fortress_ba_ro;
--     SELECT current_user, session_user;          -- expect fortress_ba_ro
--     SELECT count(*) FROM compliance_rules;      -- expect a number
--     INSERT INTO compliance_rules (rule_id) VALUES ('boom');
--       -- expect: ERROR: permission denied for table compliance_rules
--     RESET ROLE;
-- ---------------------------------------------------------------------------
