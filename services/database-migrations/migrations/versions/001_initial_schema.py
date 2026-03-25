"""Initial schema — all 8 tables.

Revision ID: 001
Revises:
Create Date: 2026-03-25

Implementation notes:
- All enum types are created via DO blocks (idempotent, no SQLAlchemy type-event
  interference). The sa.Enum(create_type=False) approach is unreliable with
  SQLAlchemy 2.0 + psycopg3: the driver still fires the before_create DDL event
  and attempts CREATE TYPE with empty values, ignoring create_type=False.
- All tables with enum columns are created via op.execute() with raw SQL to avoid
  the same issue entirely.
- Tables without enum columns use op.create_table() normally.
- server_default for enum columns uses quoted string literals ('reader', etc.)
  as required by PostgreSQL — unquoted identifiers would fail.
- All FK constraints declare ON DELETE RESTRICT — soft-deletes only.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # Enum types — DO blocks are idempotent: silently skip if type exists.
    # Using raw SQL bypasses all SQLAlchemy type-event machinery.
    # ------------------------------------------------------------------
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE reporole AS ENUM ('admin', 'author', 'reviewer', 'reader');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE nodetype AS ENUM ('blob', 'tree');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE commitstatus AS ENUM (
                'pending', 'approved', 'rejected', 'sibling_rejected', 'cancelled'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE draftstatus AS ENUM (
                'editing', 'committing', 'pending', 'approved', 'rejected',
                'sibling_rejected', 'needs_rebase', 'reconstructing', 'deleted'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
    """)

    # ------------------------------------------------------------------
    # repo_heads  (Table 2 — Repository Metadata)
    # No enum columns — op.create_table is safe here.
    # ------------------------------------------------------------------
    op.create_table(
        "repo_heads",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("repo_name", sa.String(length=255), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=2048), nullable=True),
        sa.Column("latest_commit_hash", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "repo_name", name="uq_repo_heads_owner_name"),
    )
    op.create_index("ix_repo_heads_owner_id", "repo_heads", ["owner_id"])
    op.create_index("ix_repo_heads_latest_commit_hash", "repo_heads", ["latest_commit_hash"])

    # ------------------------------------------------------------------
    # user_repo_links  (Table 1) — has reporole enum column → raw SQL
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE user_repo_links (
            id          SERIAL PRIMARY KEY,
            repo_id     UUID        NOT NULL REFERENCES repo_heads(id) ON DELETE RESTRICT,
            user_id     TEXT        NOT NULL,
            role        reporole    NOT NULL DEFAULT 'reader',
            created_at  TIMESTAMPTZ DEFAULT NOW(),
            CONSTRAINT uq_user_repo_links_repo_user UNIQUE (repo_id, user_id)
        )
    """)
    op.execute("CREATE INDEX ix_user_repo_links_repo_id   ON user_repo_links (repo_id)")
    op.execute("CREATE INDEX ix_user_repo_links_user_id   ON user_repo_links (user_id)")
    op.execute("CREATE INDEX ix_user_repo_links_repo_user ON user_repo_links (repo_id, user_id)")

    # ------------------------------------------------------------------
    # repo_tree_roots  (Table 3 — Trees) — no enum columns
    # ------------------------------------------------------------------
    op.create_table(
        "repo_tree_roots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tree_hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tree_hash"),
    )
    op.create_index("ix_repo_tree_roots_tree_hash", "repo_tree_roots", ["tree_hash"])

    # ------------------------------------------------------------------
    # repo_tree_entries  (Table 4) — has nodetype enum column → raw SQL
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE repo_tree_entries (
            id           SERIAL PRIMARY KEY,
            tree_id      INTEGER     NOT NULL REFERENCES repo_tree_roots(id) ON DELETE RESTRICT,
            type         nodetype    NOT NULL,
            name         VARCHAR(255) NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            content_type TEXT        NOT NULL DEFAULT 'text/plain',
            size         INTEGER     NOT NULL DEFAULT 0
        )
    """)
    op.execute("CREATE INDEX ix_repo_tree_entries_tree_id ON repo_tree_entries (tree_id)")

    # ------------------------------------------------------------------
    # blobs  (Table 6 — S3-backed content registry) — no enum columns
    # ------------------------------------------------------------------
    op.create_table(
        "blobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("blob_hash", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("content_type", sa.String(), nullable=False, server_default=sa.text("'text/plain'")),
        sa.Column("s3_key", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("blob_hash"),
    )
    op.create_index("ix_blobs_blob_hash", "blobs", ["blob_hash"])

    # ------------------------------------------------------------------
    # repo_commits  (Table 5) — has commitstatus enum column → raw SQL
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE repo_commits (
            id                  SERIAL PRIMARY KEY,
            commit_hash         VARCHAR(64)  NOT NULL,
            repo_id             UUID         NOT NULL REFERENCES repo_heads(id) ON DELETE RESTRICT,
            owner_id            VARCHAR(64)  NOT NULL,
            parent_commit_hash  VARCHAR(64),
            tree_id             INTEGER      NOT NULL REFERENCES repo_tree_roots(id) ON DELETE RESTRICT,
            status              commitstatus NOT NULL DEFAULT 'pending',
            timestamp           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_repo_commits_hash UNIQUE (commit_hash)
        )
    """)
    op.execute("CREATE INDEX ix_repo_commits_repo_id_status ON repo_commits (repo_id, status)")
    op.execute("CREATE INDEX ix_repo_commits_owner_id       ON repo_commits (owner_id)")

    # ------------------------------------------------------------------
    # invite_tokens  (Table 7) — has reporole enum column → raw SQL
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE invite_tokens (
            id             UUID        PRIMARY KEY,
            repo_id        UUID        NOT NULL REFERENCES repo_heads(id) ON DELETE RESTRICT,
            invited_email  VARCHAR(254) NOT NULL,
            role           reporole    NOT NULL DEFAULT 'reader',
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at     TIMESTAMPTZ NOT NULL,
            consumed_at    TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX ix_invite_tokens_repo_id       ON invite_tokens (repo_id)")
    op.execute("CREATE INDEX ix_invite_tokens_invited_email ON invite_tokens (invited_email)")

    # ------------------------------------------------------------------
    # drafts  (Table 8) — has draftstatus enum column → raw SQL
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE drafts (
            id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            repo_id           UUID        NOT NULL REFERENCES repo_heads(id) ON DELETE RESTRICT,
            author_id         VARCHAR(64) NOT NULL,
            base_commit_hash  VARCHAR(64),
            status            draftstatus NOT NULL DEFAULT 'editing',
            created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX ix_drafts_repo_id    ON drafts (repo_id)")
    op.execute("CREATE INDEX ix_drafts_author_id  ON drafts (author_id)")
    op.execute("CREATE INDEX ix_drafts_repo_author ON drafts (repo_id, author_id)")


def downgrade() -> None:
    op.drop_table("drafts")
    op.drop_table("invite_tokens")
    op.drop_table("repo_commits")
    op.drop_table("blobs")
    op.drop_table("repo_tree_entries")
    op.drop_table("repo_tree_roots")
    op.execute("DROP TABLE IF EXISTS user_repo_links")
    op.drop_table("repo_heads")
    op.execute("DROP TYPE IF EXISTS draftstatus")
    op.execute("DROP TYPE IF EXISTS commitstatus")
    op.execute("DROP TYPE IF EXISTS nodetype")
    op.execute("DROP TYPE IF EXISTS reporole")
