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