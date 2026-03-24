"""Initial schema — Tables 1 & 2 (user_repo_links, repo_heads) plus
workflow tables defined in shared models.

Revision ID: 001
Revises:
Create Date: 2026-03-24
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
    # repo_heads  (Table 2 — Repository Metadata)
    # ------------------------------------------------------------------
    op.create_table(
        "repo_heads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("repo_name", sa.String(length=255), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("latest_hash", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_repo_heads_owner_id", "repo_heads", ["owner_id"])
    op.create_index("ix_repo_heads_latest_hash", "repo_heads", ["latest_hash"])

    # ------------------------------------------------------------------
    # user_repo_links  (Table 1 — User-Repo Associations)
    # ------------------------------------------------------------------
    op.create_table(
        "user_repo_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("repo_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("admin", "author", "reviewer", "reader", name="reporole"),
            nullable=False,
            server_default="reader",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["repo_heads.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repo_id", "user_id"),
    )
    op.create_index("ix_user_repo_links_repo_id", "user_repo_links", ["repo_id"])
    op.create_index("ix_user_repo_links_user_id", "user_repo_links", ["user_id"])
    # Composite index covering the hottest query path (role lookup by repo + user)
    op.create_index(
        "ix_user_repo_links_repo_user",
        "user_repo_links",
        ["repo_id", "user_id"],
    )

    # ------------------------------------------------------------------
    # repo_tree_roots  (Table 3 — Trees)
    # ------------------------------------------------------------------
    op.create_table(
        "repo_tree_roots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tree_hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tree_hash"),
    )
    op.create_index("ix_repo_tree_roots_tree_hash", "repo_tree_roots", ["tree_hash"])

    # ------------------------------------------------------------------
    # repo_tree_entries  (Table 4 — Tree-Object Associations)
    # ------------------------------------------------------------------
    op.create_table(
        "repo_tree_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tree_id", sa.Integer(), nullable=False),
        sa.Column(
            "type",
            sa.Enum("blob", "tree", name="nodetype"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False, server_default="text/plain"),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["tree_id"], ["repo_tree_roots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_repo_tree_entries_tree_id", "repo_tree_entries", ["tree_id"])

    # ------------------------------------------------------------------
    # repo_commits  (Table 5 — Commit Information)
    # ------------------------------------------------------------------
    op.create_table(
        "repo_commits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("commit_hash", sa.String(length=64), nullable=False),
        sa.Column("repo_id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("parent_commit_hash", sa.String(length=64), nullable=True),
        sa.Column("tree_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "approved", "rejected", name="commitstatus"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["repo_id"], ["repo_heads.id"]),
        sa.ForeignKeyConstraint(["tree_id"], ["repo_tree_roots.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("commit_hash"),
    )
    op.create_index("ix_repo_commits_repo_id_status", "repo_commits", ["repo_id", "status"])
    op.create_index("ix_repo_commits_owner_id", "repo_commits", ["owner_id"])


def downgrade() -> None:
    op.drop_table("repo_commits")
    op.drop_table("repo_tree_entries")
    op.drop_table("repo_tree_roots")
    op.drop_table("user_repo_links")
    op.drop_table("repo_heads")
    op.execute("DROP TYPE IF EXISTS commitstatus")
    op.execute("DROP TYPE IF EXISTS nodetype")
    op.execute("DROP TYPE IF EXISTS reporole")
