"""Per-service PostgreSQL roles with least-privilege GRANT statements.

Revision ID: 008
Revises: 007

Creates three NOLOGIN roles scoped to the tables each service conceptually
owns or reads, per the architecture spec (Tables 1–8 ownership):

  identity_svc  — owns user_repo_links, invite_tokens; writes repo_heads
  repo_svc      — owns blobs, drafts
  workflow_svc  — owns repo_commits, repo_tree_roots, repo_tree_entries;
                  updates repo_heads + drafts (approval / sweep)

NOLOGIN roles cannot connect directly. Actual login users are created by
init_roles.py (docker-compose: init-db-roles) or Terraform in production.
Login users inherit these roles, so grants here flow through automatically.

AWS Well-Architected Security Pillar SEC05: Grant least privilege — reduce
permissions to only those needed to complete a task.
"""
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Create three per-service NOLOGIN roles (idempotent) ──────────────────
    for role in ("identity_svc", "repo_svc", "workflow_svc"):
        op.execute(f"""
            DO $$ BEGIN
                CREATE ROLE {role};
            EXCEPTION WHEN duplicate_object THEN NULL;
            END $$
        """)

    # ── identity_svc ─────────────────────────────────────────────────────────
    # Owns: user_repo_links (collaborators), invite_tokens (invite lifecycle)
    # Writes: repo_heads  (POST /v1/repos lives in identity-service)
    # Reads:  all other tables (for role lookups, context)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON user_repo_links TO identity_svc")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON invite_tokens   TO identity_svc")
    op.execute("GRANT SELECT, INSERT, UPDATE         ON repo_heads      TO identity_svc")
    op.execute(
        "GRANT SELECT ON drafts, blobs, repo_commits, "
        "repo_tree_roots, repo_tree_entries TO identity_svc"
    )
    # user_repo_links.id uses SERIAL → sequence grant required for INSERT
    op.execute("GRANT USAGE, SELECT ON SEQUENCE user_repo_links_id_seq TO identity_svc")

    # ── repo_svc ─────────────────────────────────────────────────────────────
    # Owns: blobs (S3 content registry, immutable), drafts (EFS lifecycle)
    # Reads: repo_heads, user_repo_links, repo_commits, trees
    # No DELETE on repo_heads, repo_commits, user_repo_links — immutable or
    # managed by identity/workflow.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON blobs  TO repo_svc")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON drafts TO repo_svc")
    op.execute(
        "GRANT SELECT ON repo_heads, user_repo_links, repo_commits, "
        "repo_tree_roots, repo_tree_entries TO repo_svc"
    )
    # blobs.id and drafts.id are UUID (gen_random_uuid()) — no sequence needed

    # ── workflow_svc ─────────────────────────────────────────────────────────
    # Owns: repo_commits (commit state machine),
    #        repo_tree_roots + repo_tree_entries (tree building on submit)
    # Updates: repo_heads.latest_commit_hash + version  (approval step 5)
    # Updates: drafts.status  (approval steps 6–8; committing-sweep daemon)
    # Reads:   user_repo_links, blobs, invite_tokens
    op.execute("GRANT SELECT, INSERT, UPDATE ON repo_commits      TO workflow_svc")
    op.execute("GRANT SELECT, INSERT         ON repo_tree_roots   TO workflow_svc")
    op.execute("GRANT SELECT, INSERT         ON repo_tree_entries TO workflow_svc")
    op.execute("GRANT SELECT, UPDATE         ON repo_heads        TO workflow_svc")
    op.execute("GRANT SELECT, UPDATE         ON drafts            TO workflow_svc")
    op.execute("GRANT SELECT ON user_repo_links, blobs, invite_tokens TO workflow_svc")
    # SERIAL sequences for INSERT operations
    op.execute("GRANT USAGE, SELECT ON SEQUENCE repo_commits_id_seq      TO workflow_svc")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE repo_tree_roots_id_seq   TO workflow_svc")
    op.execute("GRANT USAGE, SELECT ON SEQUENCE repo_tree_entries_id_seq TO workflow_svc")


def downgrade() -> None:
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public "
        "FROM identity_svc, repo_svc, workflow_svc"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public "
        "FROM identity_svc, repo_svc, workflow_svc"
    )
    op.execute("DROP ROLE IF EXISTS workflow_svc")
    op.execute("DROP ROLE IF EXISTS repo_svc")
    op.execute("DROP ROLE IF EXISTS identity_svc")
