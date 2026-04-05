"""
Tree traversal utilities shared across services.

collect_blobs() recursively traverses a commit tree using a single recursive
SQL CTE and returns a flat {relative_path: blob_hash} mapping for every file
reachable from the given root tree node.
"""
from sqlalchemy import text
from sqlmodel import Session


def collect_blobs(tree_id: int, db: Session) -> dict[str, str]:
    """
    Recursively collect all blob entries from a commit tree.

    Traverses the tree hierarchy (root → subtrees → blobs) using a single
    recursive CTE.  Returns {full_relative_path: blob_hash} for every blob
    reachable from the root tree identified by *tree_id*.

    Tree entries (NodeType.tree) are expanded recursively; they never appear
    in the returned dict.  The result is a flat path map identical in shape to
    the blob_map produced by sync-blobs.
    """
    rows = db.exec(  # type: ignore[call-overload]
        text("""
            WITH RECURSIVE tree_walk AS (
                -- Base case: direct entries under the root tree
                SELECT
                    te.type,
                    te.name,
                    te.content_hash,
                    ''::text AS path_prefix
                FROM repo_tree_entries te
                WHERE te.tree_id = :root_id

                UNION ALL

                -- Recursive case: expand each tree entry into its children
                SELECT
                    te.type,
                    te.name,
                    te.content_hash,
                    (tw.path_prefix || tw.name || '/')::text
                FROM tree_walk tw
                JOIN repo_tree_roots tr ON tr.tree_hash = tw.content_hash
                JOIN repo_tree_entries te ON te.tree_id = tr.id
                WHERE tw.type = 'tree'
            )
            SELECT path_prefix || name AS full_path, content_hash
            FROM tree_walk
            WHERE type = 'blob'
        """).bindparams(root_id=tree_id)
    ).all()

    return {row[0]: row[1] for row in rows}
