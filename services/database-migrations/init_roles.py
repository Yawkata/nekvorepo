"""
init_roles.py — Create per-service PostgreSQL login users.

Reads DATABASE_URL (admin credentials) and per-service passwords from
environment variables.  Creates login users that inherit the NOLOGIN roles
created by migration 008, giving each service exactly the table-level
permissions it needs.

  identity_svc_user  → inherits identity_svc role
  repo_svc_user      → inherits repo_svc role
  workflow_svc_user  → inherits workflow_svc role

Run once after migration 008 has been applied:
  docker-compose run --rm init-db-roles

Safe to re-run — existing users have their password updated; users that
already inherit the correct role are not re-granted (PostgreSQL is idempotent
for IN ROLE on CREATE USER; ALTER USER just refreshes the password).
"""
import os
import sys

import psycopg
from psycopg import sql

# ── Configuration from environment ──────────────────────────────────────────

# DATABASE_URL is in SQLAlchemy format (postgresql+psycopg://...).
# psycopg.connect() accepts the standard libpq URI (postgresql://...).
_admin_url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

# Mapping: login_user_name → (inherited_role, password)
# Keys and role names are hardcoded here (never user-supplied) so it is safe
# to interpolate them as SQL identifiers.
_USERS: dict[str, tuple[str, str]] = {
    "identity_svc_user": ("identity_svc", os.environ["IDENTITY_DB_PASSWORD"]),
    "repo_svc_user":     ("repo_svc",     os.environ["REPO_DB_PASSWORD"]),
    "workflow_svc_user": ("workflow_svc", os.environ["WORKFLOW_DB_PASSWORD"]),
}


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    print("Connecting to database as admin …")
    try:
        with psycopg.connect(_admin_url, autocommit=True) as conn:
            for login_user, (role, password) in _USERS.items():
                row = conn.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname = %s", (login_user,)
                ).fetchone()

                if row:
                    # User already exists — just refresh the password.
                    # DDL statements do not support bind parameters, so we use
                    # sql.Literal which lets psycopg safely escape the value as
                    # a quoted SQL string literal (e.g. 'mypassword').
                    conn.execute(
                        sql.SQL("ALTER USER {} WITH PASSWORD {}").format(
                            sql.Identifier(login_user),
                            sql.Literal(password),
                        )
                    )
                    print(f"  [updated]  {login_user} (password refreshed)")
                else:
                    # Create new login user inheriting the per-service role.
                    conn.execute(
                        sql.SQL("CREATE USER {} WITH PASSWORD {} IN ROLE {}").format(
                            sql.Identifier(login_user),
                            sql.Literal(password),
                            sql.Identifier(role),
                        )
                    )
                    print(f"  [created]  {login_user}  →  inherits role: {role}")

        print("\nAll per-service DB users are ready.")
        return 0
    except KeyError as exc:
        print(f"ERROR: missing required environment variable {exc}", file=sys.stderr)
        return 1
    except psycopg.OperationalError as exc:
        print(f"ERROR: could not connect to database: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
