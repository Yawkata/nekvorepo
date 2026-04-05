"""Change repo_commits.draft_id FK to ON DELETE SET NULL.

Previously the FK had no ON DELETE action (defaults to RESTRICT), which meant
deleting a Draft row that was still referenced by a commit would fail with a
FK violation.  Authors should be able to hard-delete their Draft rows
regardless of commit lifecycle, so we change the action to SET NULL.

The draft_id column remains nullable — it is an operational pointer used to:
  - find which EFS directory to wipe after approval
  - update the draft's status on rejection / approval

It is NOT the authoritative record of a commit's content; that responsibility
belongs to tree_id → repo_tree_roots → repo_tree_entries.

Revision ID: 006
Revises:     005
"""
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Dynamically locate and drop the existing FK (name may vary by environment)
    # then re-add it with ON DELETE SET NULL.
    op.execute("""
        DO $$
        DECLARE v_constraint text;
        BEGIN
            SELECT tc.constraint_name INTO v_constraint
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema   = kcu.table_schema
             AND tc.table_name     = kcu.table_name
            WHERE tc.table_schema    = 'public'
              AND tc.table_name      = 'repo_commits'
              AND tc.constraint_type = 'FOREIGN KEY'
              AND kcu.column_name    = 'draft_id';
            IF v_constraint IS NOT NULL THEN
                EXECUTE 'ALTER TABLE repo_commits DROP CONSTRAINT ' || quote_ident(v_constraint);
            END IF;
        END $$;
    """)
    op.execute("""
        ALTER TABLE repo_commits
        ADD CONSTRAINT repo_commits_draft_id_fkey
        FOREIGN KEY (draft_id)
        REFERENCES drafts(id)
        ON DELETE SET NULL
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        DECLARE v_constraint text;
        BEGIN
            SELECT tc.constraint_name INTO v_constraint
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema   = kcu.table_schema
             AND tc.table_name     = kcu.table_name
            WHERE tc.table_schema    = 'public'
              AND tc.table_name      = 'repo_commits'
              AND tc.constraint_type = 'FOREIGN KEY'
              AND kcu.column_name    = 'draft_id';
            IF v_constraint IS NOT NULL THEN
                EXECUTE 'ALTER TABLE repo_commits DROP CONSTRAINT ' || quote_ident(v_constraint);
            END IF;
        END $$;
    """)
    op.execute("""
        ALTER TABLE repo_commits
        ADD CONSTRAINT repo_commits_draft_id_fkey
        FOREIGN KEY (draft_id)
        REFERENCES drafts(id)
    """)
