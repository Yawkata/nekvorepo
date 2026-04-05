"""Schema cleanup — drop four redundant columns.

repo_tree_entries.content_type  — always 'text/plain' or ''; real value is in blobs.
repo_tree_entries.size          — always 0; real value is in blobs.
blobs.s3_key                    — always identical to blob_hash (spec comment confirmed this).
drafts.changes_summary          — always mirrors repo_commits.changes_summary after submission;
                                  NULL before submission. No information is lost by dropping it.

Revision ID: 007
Revises:     006
"""
import sqlalchemy as sa
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # repo_tree_entries — drop metadata columns that duplicate blobs table
    op.drop_column("repo_tree_entries", "content_type")
    op.drop_column("repo_tree_entries", "size")

    # blobs — drop s3_key which is always equal to blob_hash
    op.drop_column("blobs", "s3_key")

    # drafts — drop denormalised changes_summary (canonical copy lives on repo_commits)
    op.drop_column("drafts", "changes_summary")


def downgrade() -> None:
    op.add_column(
        "repo_tree_entries",
        sa.Column("content_type", sa.Text(), nullable=False, server_default="text/plain"),
    )
    op.add_column(
        "repo_tree_entries",
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "blobs",
        sa.Column("s3_key", sa.String(), nullable=False, server_default=""),
    )
    op.add_column(
        "drafts",
        sa.Column("changes_summary", sa.Text(), nullable=True),
    )
