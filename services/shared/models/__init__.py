"""
shared.models package.

Import from the specific sub-module, not from this package root.
Explicit imports eliminate the "any service can accidentally use any model"
problem that arises from a flat re-export namespace.

  from shared.models.identity import UserRepoLink
  from shared.models.workflow import RepoHead, RepoCommit, RepoTreeRoot, RepoTreeEntry
  from shared.models.repo     import Blob, Draft
  from shared.models.invite   import InviteToken
"""
