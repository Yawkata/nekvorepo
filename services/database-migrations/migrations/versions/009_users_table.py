"""Add users table for email lookup in member management.

Revision ID: 009
Revises: 008

The `users` table stores the Cognito sub → email mapping so that
member list endpoints can return email addresses without calling Cognito
on every request. Email is upserted on every successful login.

AWS Well-Architected Security Pillar SEC05: Least-privilege grants applied
per service, consistent with migration 008 pattern.
"""
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE users (
            id         TEXT         PRIMARY KEY,
            email      VARCHAR(254) NOT NULL,
            created_at TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_users_email UNIQUE (email)
        )
    """)
    op.execute("CREATE INDEX ix_users_email ON users (email)")

    # identity_svc — owns users table (upserts on login/accept)
    op.execute("GRANT SELECT, INSERT, UPDATE ON users TO identity_svc")

    # repo_svc and workflow_svc — read-only (member list JOIN queries)
    op.execute("GRANT SELECT ON users TO repo_svc")
    op.execute("GRANT SELECT ON users TO workflow_svc")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS users")
