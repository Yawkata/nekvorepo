from enum import Enum

class RepoRole(str, Enum):
    admin = "admin"
    author = "author"
    reviewer = "reviewer"
    reader = "reader"

class NodeType(str, Enum):
    blob = "blob"
    tree = "tree"

class CommitStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    sibling_rejected = "sibling_rejected"
    cancelled = "cancelled"

class DraftStatus(str, Enum):
    editing = "editing"
    committing = "committing"
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    sibling_rejected = "sibling_rejected"
    needs_rebase = "needs_rebase"
    reconstructing = "reconstructing"
    deleted = "deleted"