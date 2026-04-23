"use client";

import { CSSProperties, ReactNode, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";

type RepoFile = {
  name: string;
  size?: number;
  updated_at?: string;
};

type Role = "reader" | "author" | "reviewer" | "admin";

type Draft = {
  draft_id?: string;
  id?: string;
  label?: string | null;
  status?: string;
  base_commit_hash?: string | null;
  commit_hash?: string | null;
  updated_at?: string;
  created_at?: string;
};

type Commit = {
  commit_hash: string;
  status: CommitStatusValue;
  commit_summary: string;
  commit_description?: string;
  changes_summary?: string;
  owner_id: string;
  timestamp?: string;
  draft_id?: string;
  reviewer_comment?: string;
  parent_commit_hash?: string;
};

type CommitStatusValue =
  | "pending"
  | "approved"
  | "rejected"
  | "sibling_rejected"
  | "cancelled";

type CommitStatusInfo = {
  commit_hash: string;
  status: CommitStatusValue;
  reviewer_comment?: string | null;
  timestamp?: string;
};

type RepoHead = {
  repo_id: string;
  latest_commit_hash: string | null;
  commit_timestamp: string | null;
};

type ConflictMode = "rebase" | "sibling";

type ConflictFile = {
  path: string;
  category: string;
  has_draft_changes: boolean;
  draft_hash?: string | null;
  head_hash?: string | null;
  base_hash?: string | null;
};

type ConflictResolution = "keep_mine" | "use_theirs";

type ConflictReview = {
  draft: Draft;
  draftId: string;
  draftTitle: string;
  mode: ConflictMode;
  pinnedHead: string;
  baseCommitHash: string | null;
  files: ConflictFile[];
  resolutions: Record<string, ConflictResolution>;
  // Optional "save_as" target per type_collision root, used only when the
  // root's resolution is "use_theirs" and the author wants to preserve their
  // file content under a new path before HEAD wins.
  saveAs: Record<string, string>;
  // Roots of each type_collision group — only these paths require an
  // explicit resolution from the author. Mirrors the backend algorithm
  // (see _find_collision_roots in rebase.py).
  collisionRoots: string[];
};

type Invite = {
  token_id: string;
  invited_email: string;
  role: string;
  created_at?: string;
  expires_at?: string;
};

type Member = {
  user_id: string;
  email?: string | null;
  role: string;
  joined_at?: string;
};

type Repo = {
  repo_id: string;
  repo_name: string;
  description?: string;
  owner_id?: string;
  owner_email?: string;
  owner_name?: string;
  full_name?: string;
  created_at?: string;
  updated_at?: string;
  visibility?: string;
  role?: Role | string;
  files?: RepoFile[];
};

type ToolbarAction = {
  label: string;
  variant?: "primary" | "secondary" | "danger";
  onClick?: () => void;
};

function actionsForRole(
  role: string | undefined,
  handlers: {
    onNewDraft: () => void;
    onInviteMember: () => void;
    inviting: boolean;
    onDeleteRepo: () => void;
    deletingRepo: boolean;
  }
): ToolbarAction[] {
  const normalized = (role || "").toLowerCase();
  const newDraft: ToolbarAction = {
    label: "+ New Draft",
    variant: "primary",
    onClick: handlers.onNewDraft,
  };
  const inviteMember: ToolbarAction = {
    label: handlers.inviting ? "Inviting..." : "Invite Member",
    variant: "secondary",
    onClick: handlers.onInviteMember,
  };
  switch (normalized) {
    case "admin":
      return [
        newDraft,
        inviteMember,
        { label: "Download ZIP", variant: "secondary" },
        {
          label: handlers.deletingRepo ? "Deleting..." : "Delete Repository",
          variant: "danger",
          onClick: handlers.onDeleteRepo,
        },
      ];
    case "author":
      return [
        newDraft,
        { label: "Download ZIP", variant: "secondary" },
      ];
    case "reviewer":
      return [
        { label: "Download ZIP", variant: "primary" },
      ];
    case "reader":
    default:
      return [{ label: "Download ZIP", variant: "primary" }];
  }
}

function formatDate(value?: string): string {
  if (!value) return "—";
  const d = new Date(value);
  if (isNaN(d.getTime())) return value;
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function formatDateTime(value?: string): string {
  if (!value) return "—";
  const d = new Date(value);
  if (isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function extractErrorMessage(data: any, fallback: string): string {
  if (!data) return fallback;
  const d = data.detail ?? data.error;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d
      .map((item) =>
        typeof item === "string"
          ? item
          : item?.msg
          ? `${Array.isArray(item.loc) ? item.loc.join(".") + ": " : ""}${item.msg}`
          : JSON.stringify(item)
      )
      .join("; ");
  }
  if (d && typeof d === "object") return d.msg || JSON.stringify(d);
  return fallback;
}

function formatSize(bytes?: number): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function getOwnerDisplay(repo: Repo | null): string {
  if (!repo) return "—";
  // Prefer full_name, then owner_name, then extract from email, then use owner_id
  if (repo.full_name) return repo.full_name;
  if (repo.owner_name) return repo.owner_name;
  if (repo.owner_email) {
    // Extract the part before @ as username, or return full email if no @
    const atIndex = repo.owner_email.indexOf("@");
    return atIndex > 0 ? repo.owner_email.substring(0, atIndex) : repo.owner_email;
  }
  return repo.owner_id || "—";
}

export default function RepoPage() {
  const router = useRouter();
  const params = useParams();
  const repoId = params?.repo_id as string;

  const [repo, setRepo] = useState<Repo | null>(null);
  const [repoHead, setRepoHead] = useState<RepoHead | null>(null);
  const [conflictReview, setConflictReview] = useState<ConflictReview | null>(null);
  const [conflictLoadingDraftId, setConflictLoadingDraftId] = useState<string | null>(null);
  const [conflictError, setConflictError] = useState<string | null>(null);
  const [rebasingDraftId, setRebasingDraftId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [creatingDraft, setCreatingDraft] = useState(false);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [draftsLoading, setDraftsLoading] = useState(true);
  const [draftsError, setDraftsError] = useState<string | null>(null);
  const [deleteMode, setDeleteMode] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [renameMode, setRenameMode] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [commitMode, setCommitMode] = useState(false);

  // Commits state
  const [commits, setCommits] = useState<Commit[]>([]);
  const [commitsLoading, setCommitsLoading] = useState(false);
  const [commitsError, setCommitsError] = useState<string | null>(null);
  const [submittingCommit, setSubmittingCommit] = useState(false);
  const [approvingHash, setApprovingHash] = useState<string | null>(null);
  const [rejectingHash, setRejectingHash] = useState<string | null>(null);
  const [openCommitMenu, setOpenCommitMenu] = useState<string | null>(null);
  const [commitStatuses, setCommitStatuses] = useState<
    Record<string, CommitStatusInfo>
  >({});
  const [statusLoadingHash, setStatusLoadingHash] = useState<string | null>(null);

  // Commit history state
  const [history, setHistory] = useState<Commit[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

  // Collapsible section state
  const [draftsCollapsed, setDraftsCollapsed] = useState(false);
  const [pendingCollapsed, setPendingCollapsed] = useState(false);
  const [historyCollapsed, setHistoryCollapsed] = useState(false);

  // Committed view state (accepted commit files)
  type ViewFile = { path: string; content_type: string; size: number };
  const [viewFiles, setViewFiles] = useState<ViewFile[]>([]);
  const [viewCommitHash, setViewCommitHash] = useState<string | null>(null);
  const [selectedCommit, setSelectedCommit] = useState<Commit | null>(null);
  const [viewLoading, setViewLoading] = useState(false);
  const [viewError, setViewError] = useState<string | null>(null);
  const [expandedFolders, setExpandedFolders] = useState<Set<string>>(
    new Set([""])
  );

  // File viewer modal state
  type OpenFile = {
    path: string;
    content_type: string;
    size: number;
    url: string;
    text: string | null; // null if not a text file (use download link instead)
    loadingText: boolean;
  };
  const [openedFile, setOpenedFile] = useState<OpenFile | null>(null);
  const [openingFilePath, setOpeningFilePath] = useState<string | null>(null);
  const [inviting, setInviting] = useState(false);
  const [deletingRepo, setDeletingRepo] = useState(false);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [invitesLoading, setInvitesLoading] = useState(false);
  const [invitesError, setInvitesError] = useState<string | null>(null);
  const [invitesOpen, setInvitesOpen] = useState(true);
  const [acceptingInviteId, setAcceptingInviteId] = useState<string | null>(null);
  const [resendingInviteId, setResendingInviteId] = useState<string | null>(null);
  const [revokingInviteId, setRevokingInviteId] = useState<string | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [membersError, setMembersError] = useState<string | null>(null);
  const [membersOpen, setMembersOpen] = useState(true);
  const [changingRoleUserId, setChangingRoleUserId] = useState<string | null>(null);
  const [removingMemberId, setRemovingMemberId] = useState<string | null>(null);
  const [currentUserEmail, setCurrentUserEmail] = useState<string | null>(null);

  const isAdmin = String(repo?.role || "").toLowerCase() === "admin";
  const normalizedCurrentUserEmail = currentUserEmail?.trim().toLowerCase();
  const latestHeadHash = repoHead?.latest_commit_hash ?? null;
  const staleDrafts = useMemo(
    () => drafts.filter((draft) => isDraftStale(draft, latestHeadHash)),
    [drafts, latestHeadHash]
  );
  // Approved drafts are shown in the Commit History section, not here.
  const visibleDrafts = useMemo(
    () =>
      drafts.filter(
        (d) => (d.status || "").toLowerCase() !== "approved"
      ),
    [drafts]
  );

  function isDraftStale(draft: Draft, headHash: string | null): boolean {
    if (draft.status === "needs_rebase" || draft.status === "sibling_rejected") {
      return true;
    }
    if (!headHash) return false;
    if (draft.status && !["editing", "rejected"].includes(draft.status)) {
      return false;
    }
    return (draft.base_commit_hash ?? null) !== headHash;
  }

  function conflictModeForDraft(draft: Draft): ConflictMode {
    return draft.status === "sibling_rejected" ? "sibling" : "rebase";
  }

  // Mirrors backend _find_collision_roots: a root is a type_collision path
  // with no strictly-shorter type_collision path as a prefix.
  function computeCollisionRoots(files: ConflictFile[]): string[] {
    const collisionSet = new Set(
      files.filter((f) => f.category === "type_collision").map((f) => f.path)
    );
    const roots: string[] = [];
    for (const path of collisionSet) {
      const parts = path.split("/");
      let isRoot = true;
      for (let i = 1; i < parts.length; i++) {
        if (collisionSet.has(parts.slice(0, i).join("/"))) {
          isRoot = false;
          break;
        }
      }
      if (isRoot) roots.push(path);
    }
    return roots.sort();
  }

  function findCollisionRootFor(path: string, roots: string[]): string | null {
    if (roots.includes(path)) return path;
    for (const root of roots) {
      if (path.startsWith(root + "/")) return root;
    }
    return null;
  }

  function isConflictActionRequired(
    file: ConflictFile,
    collisionRoots: string[]
  ): boolean {
    if (file.category === "conflict") return true;
    if (file.category === "deleted_in_head" && file.has_draft_changes)
      return true;
    if (file.category === "type_collision") {
      return collisionRoots.includes(file.path);
    }
    return false;
  }

  function conflictCategoryColor(category: string): string {
    switch (category) {
      case "conflict":
      case "type_collision":
        return "#ff6b6b";
      case "deleted_in_head":
        return "#ffa94d";
      case "added_in_head":
        return "#4fc3f7";
      case "no_conflict":
        return "#51cf66";
      default:
        return MUTED;
    }
  }

  function requiredConflictPaths(
    files: ConflictFile[],
    collisionRoots: string[]
  ): string[] {
    return files
      .filter((file) => isConflictActionRequired(file, collisionRoots))
      .map((file) => file.path);
  }

  function setConflictSaveAs(path: string, saveAs: string) {
    setConflictReview((current) =>
      current
        ? {
            ...current,
            saveAs: { ...current.saveAs, [path]: saveAs },
          }
        : current
    );
  }

  function setConflictResolution(path: string, resolution: ConflictResolution) {
    setConflictReview((current) =>
      current
        ? {
            ...current,
            resolutions: {
              ...current.resolutions,
              [path]: resolution,
            },
          }
        : current
    );
  }

  async function loadInvites() {
    const token = localStorage.getItem("token");
    if (!token || !repoId) return;

    setInvitesLoading(true);
    setInvitesError(null);
    try {
      const res = await fetch(`/api/repos/${repoId}/invites`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      const text = await res.text();
      const data = text ? JSON.parse(text) : [];
      if (!res.ok) {
        setInvitesError(extractErrorMessage(data, "Failed to load invites"));
        return;
      }
      setInvites(Array.isArray(data) ? data : []);
    } catch {
      setInvitesError("Failed to connect to server");
    } finally {
      setInvitesLoading(false);
    }
  }

  async function loadMembers() {
    const token = localStorage.getItem("token");
    if (!token || !repoId) return;

    setMembersLoading(true);
    setMembersError(null);
    try {
      const res = await fetch(`/api/repos/${repoId}/members`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      const text = await res.text();
      const data = text ? JSON.parse(text) : [];
      if (!res.ok) {
        setMembersError(extractErrorMessage(data, "Failed to load members"));
        return;
      }
      setMembers(Array.isArray(data) ? data : []);
    } catch {
      setMembersError("Failed to connect to server");
    } finally {
      setMembersLoading(false);
    }
  }

  async function handleInviteMember() {
    if (inviting || !repoId) return;
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }

    const email = window.prompt("Email address to invite:", "")?.trim();
    if (!email) return;

    const roleInput = window
      .prompt("Role for the invited member: reader, author, reviewer, or admin", "reader")
      ?.trim()
      .toLowerCase();
    if (!roleInput) return;
    if (!["reader", "author", "reviewer", "admin"].includes(roleInput)) {
      alert("Role must be reader, author, reviewer, or admin.");
      return;
    }

    setInviting(true);
    try {
      const res = await fetch(`/api/repos/${repoId}/invites`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ email, role: roleInput }),
      });
      const text = await res.text();
      let data: any = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        /* ignore */
      }
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      if (!res.ok) {
        alert(extractErrorMessage(data, "Failed to send invite"));
        return;
      }
      await loadInvites();
    } catch {
      alert("Failed to connect to server");
    } finally {
      setInviting(false);
    }
  }

  async function handleAcceptInvite(invite: Invite) {
    if (acceptingInviteId || !repoId) return;
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }

    setAcceptingInviteId(invite.token_id);
    try {
      const res = await fetch(`/api/repos/${repoId}/invites/${invite.token_id}/accept`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      const text = await res.text();
      let data: any = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        /* ignore */
      }
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      if (!res.ok) {
        alert(extractErrorMessage(data, "Failed to accept invite"));
        return;
      }
      setInvites((prev) => prev.filter((item) => item.token_id !== invite.token_id));
      await loadMembers();
    } catch {
      alert("Failed to connect to server");
    } finally {
      setAcceptingInviteId(null);
    }
  }

  async function handleResendInvite(invite: Invite) {
    if (resendingInviteId || !repoId) return;
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }

    setResendingInviteId(invite.token_id);
    try {
      const res = await fetch(`/api/repos/${repoId}/invites/${invite.token_id}/resend`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      const text = await res.text();
      let data: any = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        /* ignore */
      }
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      if (!res.ok) {
        alert(extractErrorMessage(data, "Failed to resend invite"));
        return;
      }
      await loadInvites();
    } catch {
      alert("Failed to connect to server");
    } finally {
      setResendingInviteId(null);
    }
  }

  async function handleRevokeInvite(invite: Invite) {
    if (revokingInviteId || !repoId) return;
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }
    if (!confirm(`Revoke invite for ${invite.invited_email}?`)) return;

    setRevokingInviteId(invite.token_id);
    try {
      const res = await fetch(`/api/repos/${repoId}/invites/${invite.token_id}/revoke`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      const text = await res.text();
      let data: any = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        /* ignore */
      }
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      if (!res.ok) {
        alert(extractErrorMessage(data, "Failed to revoke invite"));
        return;
      }
      setInvites((prev) => prev.filter((item) => item.token_id !== invite.token_id));
    } catch {
      alert("Failed to connect to server");
    } finally {
      setRevokingInviteId(null);
    }
  }

  async function handleChangeMemberRole(member: Member) {
    if (changingRoleUserId || !repoId) return;
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }

    const roleInput = window
      .prompt("New role: reader, author, reviewer, or admin", member.role)
      ?.trim()
      .toLowerCase();
    if (!roleInput || roleInput === member.role) return;
    if (!["reader", "author", "reviewer", "admin"].includes(roleInput)) {
      alert("Role must be reader, author, reviewer, or admin.");
      return;
    }

    setChangingRoleUserId(member.user_id);
    try {
      const res = await fetch(
        `/api/repos/${repoId}/members/${encodeURIComponent(member.user_id)}/role`,
        {
          method: "PUT",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ role: roleInput }),
        }
      );
      const text = await res.text();
      let data: any = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        /* ignore */
      }
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      if (!res.ok) {
        alert(extractErrorMessage(data, "Failed to change role"));
        return;
      }
      setMembers((prev) =>
        prev.map((item) =>
          item.user_id === member.user_id ? { ...item, role: data.role || roleInput } : item
        )
      );
    } catch {
      alert("Failed to connect to server");
    } finally {
      setChangingRoleUserId(null);
    }
  }

  async function handleRemoveMember(member: Member) {
    if (removingMemberId || !repoId) return;
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }
    const label = member.email || member.user_id;
    if (!confirm(`Remove ${label} from this repository?`)) return;

    setRemovingMemberId(member.user_id);
    try {
      const res = await fetch(
        `/api/repos/${repoId}/members/${encodeURIComponent(member.user_id)}`,
        {
          method: "DELETE",
          headers: { Authorization: `Bearer ${token}` },
        }
      );
      const text = await res.text();
      let data: any = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        /* ignore */
      }
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      if (!res.ok) {
        alert(extractErrorMessage(data, "Failed to remove member"));
        return;
      }
      setMembers((prev) => prev.filter((item) => item.user_id !== member.user_id));
    } catch {
      alert("Failed to connect to server");
    } finally {
      setRemovingMemberId(null);
    }
  }

  async function handleRenameDraft(draftId: string, currentTitle: string) {
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }
    const raw = window.prompt(
      `New name for "${currentTitle}":`,
      currentTitle
    );
    if (raw === null) return; // cancelled
    const label = raw.trim();
    if (!label) {
      alert("Draft name cannot be empty.");
      return;
    }
    if (label.length > 100) {
      alert("Draft name must be 100 characters or fewer.");
      return;
    }
    if (label === currentTitle) return;

    setRenamingId(draftId);
    try {
      const res = await fetch(`/api/repos/${repoId}/drafts/${draftId}`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ label }),
      });
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      const text = await res.text();
      let data: any = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        /* ignore */
      }
      if (!res.ok) {
        alert(extractErrorMessage(data, "Failed to rename draft"));
        return;
      }
      setDrafts((prev) =>
        prev.map((d) =>
          (d.draft_id ?? d.id) === draftId ? { ...d, label } : d
        )
      );
    } catch {
      alert("Failed to connect to server");
    } finally {
      setRenamingId(null);
    }
  }

  async function handleDeleteDraft(draftId: string, title: string) {
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }
    if (!confirm(`Delete draft "${title}"? This cannot be undone.`)) return;

    setDeletingId(draftId);
    try {
      const res = await fetch(`/api/repos/${repoId}/drafts/${draftId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      if (!res.ok) {
        const text = await res.text();
        let data: any = {};
        try {
          data = text ? JSON.parse(text) : {};
        } catch {
          /* ignore */
        }
        alert(extractErrorMessage(data, "Failed to delete draft"));
        return;
      }
      setDrafts((prev) =>
        prev.filter((d) => (d.draft_id ?? d.id) !== draftId)
      );
    } catch {
      alert("Failed to connect to server");
    } finally {
      setDeletingId(null);
    }
  }

  async function loadDrafts() {
    const token = localStorage.getItem("token");
    if (!token || !repoId) return;

    setDraftsLoading(true);
    setDraftsError(null);
    try {
      const res = await fetch(`/api/repos/${repoId}/drafts`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      const text = await res.text();
      const data = text ? JSON.parse(text) : [];
      if (!res.ok) {
        setDraftsError(extractErrorMessage(data, "Failed to load drafts"));
        return;
      }
      setDrafts(Array.isArray(data) ? data : []);
    } catch {
      setDraftsError("Failed to connect to server");
    } finally {
      setDraftsLoading(false);
    }
  }

  async function loadRepoHead(signal?: AbortSignal) {
    const token = localStorage.getItem("token");
    if (!token || !repoId) return;

    try {
      const res = await fetch(`/api/repos/${repoId}/head`, {
        headers: { Authorization: `Bearer ${token}` },
        signal,
      });
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      const text = await res.text();
      let data: unknown = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        /* ignore parse errors */
      }
      if (!res.ok) return;

      const nextHead = data as RepoHead;
      setRepoHead((prev) => {
        const previousHash = prev?.latest_commit_hash ?? null;
        const nextHash = nextHead.latest_commit_hash ?? null;
        if (previousHash !== null && previousHash !== nextHash) {
          void loadDrafts();
          void loadHistory();
          void loadView();
        }
        return nextHead;
      });
    } catch (err) {
      // AbortError on teardown / repo change is expected — swallow silently.
      if ((err as { name?: string } | null)?.name === "AbortError") return;
      /* Keep polling quietly; the next interval can recover. */
    }
  }

  async function handleOpenConflictReview(draft: Draft, title: string) {
    const draftId = draft.draft_id ?? draft.id;
    if (!draftId || !repoId) return;

    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }
    if (!latestHeadHash) {
      alert("There is no accepted HEAD commit to review against yet.");
      return;
    }

    const mode = conflictModeForDraft(draft);
    setConflictLoadingDraftId(draftId);
    setConflictError(null);
    try {
      const res = await fetch(`/api/repos/${repoId}/conflicts`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          draft_id: draftId,
          head: latestHeadHash,
          mode,
        }),
      });
      if (res.status === 401) {
        router.push("/login");
        return;
      }

      const text = await res.text();
      let data: unknown = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        /* ignore parse errors */
      }

      if (!res.ok) {
        const message = extractErrorMessage(
          data,
          "Failed to classify conflicts"
        );
        setConflictError(message);
        alert(message);
        return;
      }

      const result = data as {
        base_commit_hash?: string | null;
        head_commit_hash?: string;
        files?: ConflictFile[];
      };
      const files = Array.isArray(result.files) ? result.files : [];
      setConflictReview({
        draft,
        draftId,
        draftTitle: title,
        mode,
        pinnedHead: result.head_commit_hash || latestHeadHash,
        baseCommitHash: result.base_commit_hash ?? draft.base_commit_hash ?? null,
        files,
        resolutions: {},
        saveAs: {},
        collisionRoots: computeCollisionRoots(files),
      });
    } catch {
      setConflictError("Failed to connect to server");
      alert("Failed to connect to server");
    } finally {
      setConflictLoadingDraftId(null);
    }
  }

  async function handleFinalizeRebase() {
    if (!conflictReview || rebasingDraftId) return;

    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }

    const requiredPaths = requiredConflictPaths(
      conflictReview.files,
      conflictReview.collisionRoots
    );
    const missing = requiredPaths.filter(
      (path) => !conflictReview.resolutions[path]
    );
    if (missing.length > 0) {
      setConflictError(
        `Choose a resolution for ${missing.length} file${
          missing.length === 1 ? "" : "s"
        } before finalizing.`
      );
      return;
    }

    setRebasingDraftId(conflictReview.draftId);
    setConflictError(null);
    try {
      const collisionRootSet = new Set(conflictReview.collisionRoots);
      const resolutions = requiredPaths.map((path) => {
        const choice = conflictReview.resolutions[path];
        const entry: {
          path: string;
          resolution: ConflictResolution;
          save_as?: string;
        } = { path, resolution: choice };
        // save_as is only valid for type_collision roots with use_theirs
        if (
          choice === "use_theirs" &&
          collisionRootSet.has(path)
        ) {
          const saveAs = (conflictReview.saveAs[path] || "").trim();
          if (saveAs) entry.save_as = saveAs;
        }
        return entry;
      });

      const res = await fetch(
        `/api/repos/${repoId}/drafts/${conflictReview.draftId}/rebase`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            expected_head_commit_hash: conflictReview.pinnedHead,
            resolutions,
          }),
        }
      );
      if (res.status === 401) {
        router.push("/login");
        return;
      }

      const text = await res.text();
      let data: unknown = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        /* ignore parse errors */
      }

      if (res.status === 409) {
        const detail = (data as { detail?: { error?: string; new_head_commit_hash?: string | null } }).detail;
        if (detail?.error === "head_moved_again") {
          setConflictError(
            "HEAD advanced again while this review was open. Reloading the latest state."
          );
          setRepoHead((current) =>
            current
              ? {
                  ...current,
                  latest_commit_hash: detail.new_head_commit_hash ?? null,
                }
              : current
          );
          await loadDrafts();
          await loadRepoHead();
          return;
        }
      }

      if (res.status === 422) {
        const detail = (
          data as {
            detail?: {
              error?: string;
              paths?: string[];
              message?: string;
            };
          }
        ).detail;
        if (detail?.error === "missing_resolutions") {
          const paths = Array.isArray(detail.paths) ? detail.paths : [];
          setConflictError(
            `Missing resolutions for: ${paths.join(", ")}`
          );
          return;
        }
      }

      if (!res.ok) {
        setConflictError(extractErrorMessage(data, "Failed to finalize rebase"));
        return;
      }

      setConflictReview(null);
      await loadDrafts();
      await loadRepoHead();
      await loadView();
    } catch {
      setConflictError("Failed to connect to server");
    } finally {
      setRebasingDraftId(null);
    }
  }

  async function fetchCommitStatus(
    commitHash: string,
    token: string
  ): Promise<CommitStatusInfo | null> {
    const res = await fetch(`/api/repos/${repoId}/commits/${commitHash}/status`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (res.status === 401) {
      router.push("/login");
      return null;
    }
    const text = await res.text();
    const data = text ? JSON.parse(text) : {};
    if (!res.ok) return null;
    return data as CommitStatusInfo;
  }

  async function refreshCommitStatus(commitHash: string) {
    const token = localStorage.getItem("token");
    if (!token || !repoId) return;

    setStatusLoadingHash(commitHash);
    try {
      const data = await fetchCommitStatus(commitHash, token);
      if (!data) return;
      setCommitStatuses((prev) => ({
        ...prev,
        [commitHash]: data,
      }));
      if (data.status !== "pending") {
        setCommits((prev) =>
          prev.map((commit) =>
            commit.commit_hash === commitHash
              ? {
                  ...commit,
                  status: data.status,
                  reviewer_comment: data.reviewer_comment ?? undefined,
                  timestamp: data.timestamp ?? commit.timestamp,
                }
              : commit
          )
        );
      }
    } catch {
      alert("Failed to load commit status");
    } finally {
      setStatusLoadingHash(null);
    }
  }

  async function refreshCommitStatuses(list: Commit[]) {
    const token = localStorage.getItem("token");
    if (!token || !repoId || list.length === 0) return;

    const results = await Promise.all(
      list.map((commit) =>
        fetchCommitStatus(commit.commit_hash, token).catch(() => null)
      )
    );
    const next: Record<string, CommitStatusInfo> = {};
    results.forEach((result) => {
      if (result) next[result.commit_hash] = result;
    });
    if (Object.keys(next).length > 0) {
      setCommitStatuses((prev) => ({ ...prev, ...next }));
    }
  }

  async function loadCommits() {
    const token = localStorage.getItem("token");
    if (!token || !repoId) return;

    setCommitsLoading(true);
    setCommitsError(null);
    try {
      const res = await fetch(`/api/repos/${repoId}/commits`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      const text = await res.text();
      let data: any = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        /* ignore parse errors */
      }
      if (!res.ok) {
        setCommitsError(extractErrorMessage(data, "Failed to load commits"));
        return;
      }
      // Backend returns a plain array; fall back to data.commits for safety
      const list = Array.isArray(data)
        ? data
        : Array.isArray(data?.commits)
        ? data.commits
        : [];
      setCommits(list);
      setCommitStatuses(
        Object.fromEntries(
          (list as Commit[]).map((commit) => [
            commit.commit_hash,
            {
              commit_hash: commit.commit_hash,
              status: commit.status,
              reviewer_comment: commit.reviewer_comment ?? null,
              timestamp: commit.timestamp,
            },
          ])
        )
      );
      void refreshCommitStatuses(list as Commit[]);
    } catch {
      setCommitsError("Failed to connect to server");
    } finally {
      setCommitsLoading(false);
    }
  }

  function toggleFolder(folderPath: string) {
    setExpandedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(folderPath)) next.delete(folderPath);
      else next.add(folderPath);
      return next;
    });
  }

  function isTextContentType(contentType: string): boolean {
    if (!contentType) return false;
    const ct = contentType.toLowerCase();
    if (ct.startsWith("text/")) return true;
    if (ct.includes("json")) return true;
    if (ct.includes("xml")) return true;
    if (ct.includes("javascript")) return true;
    if (ct.includes("yaml")) return true;
    if (ct.includes("csv")) return true;
    if (ct.includes("markdown")) return true;
    return false;
  }

  async function handleOpenViewFile(file: ViewFile) {
    const token = localStorage.getItem("token");
    if (!token || !repoId) return;

    setOpeningFilePath(file.path);
    try {
      // Split the path into segments and encode each one for the dynamic route
      const segments = file.path.split("/").map(encodeURIComponent).join("/");
      const query = viewCommitHash
        ? `?ref=${encodeURIComponent(viewCommitHash)}`
        : "";
      const res = await fetch(
        `/api/repos/${repoId}/files/${segments}${query}`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      const text = await res.text();
      let data: { url?: string; content_type?: string; size?: number } = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        /* ignore */
      }
      if (!res.ok || !data.url) {
        alert(extractErrorMessage(data, "Failed to open file"));
        return;
      }

      const opened: OpenFile = {
        path: file.path,
        content_type: data.content_type || file.content_type,
        size: data.size ?? file.size,
        url: data.url,
        text: null,
        loadingText: isTextContentType(data.content_type || file.content_type),
      };
      setOpenedFile(opened);

      // Fetch text content for readable types
      if (opened.loadingText) {
        try {
          const fileRes = await fetch(opened.url);
          if (fileRes.ok) {
            const body = await fileRes.text();
            setOpenedFile((prev) =>
              prev && prev.path === file.path
                ? { ...prev, text: body, loadingText: false }
                : prev
            );
          } else {
            setOpenedFile((prev) =>
              prev && prev.path === file.path
                ? { ...prev, loadingText: false }
                : prev
            );
          }
        } catch {
          setOpenedFile((prev) =>
            prev && prev.path === file.path
              ? { ...prev, loadingText: false }
              : prev
          );
        }
      }
    } catch {
      alert("Failed to connect to server");
    } finally {
      setOpeningFilePath(null);
    }
  }

  async function loadView(ref?: string | null, commit?: Commit | null) {
    const token = localStorage.getItem("token");
    if (!token || !repoId) return;

    setViewLoading(true);
    setViewError(null);
    try {
      const query = ref ? `?ref=${encodeURIComponent(ref)}` : "";
      const res = await fetch(`/api/repos/${repoId}/view${query}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      const text = await res.text();
      let data: { commit_hash?: string | null; files?: ViewFile[] } = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        /* ignore */
      }
      if (!res.ok) {
        setViewError(
          extractErrorMessage(data, "Failed to load committed files")
        );
        return;
      }
      setViewFiles(Array.isArray(data.files) ? data.files : []);
      setViewCommitHash(data.commit_hash ?? null);
      setSelectedCommit(commit ?? null);
    } catch {
      setViewError("Failed to connect to server");
    } finally {
      setViewLoading(false);
    }
  }

  function handleViewCommit(commit: Commit) {
    const status = (commit.status || "").toLowerCase();
    // Only block pending commits (they haven't been resolved yet and belong
    // to the reviewer queue, not the history view). Approved, rejected, and
    // sibling_rejected commits all have a reconstructible tree and can be
    // inspected via /view?ref=<hash>.
    if (status === "pending") {
      alert("Pending commits aren't viewable here — use the Pending section.");
      return;
    }
    setExpandedFolders(new Set([""]));
    setOpenedFile(null);
    setSearch("");
    void loadView(commit.commit_hash, commit);
    // Scroll the main view rectangle into view for feedback.
    if (typeof window !== "undefined") {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }

  async function loadHistory() {
    const token = localStorage.getItem("token");
    if (!token || !repoId) return;

    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const res = await fetch(`/api/repos/${repoId}/commits/history`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      const text = await res.text();
      let data: unknown = [];
      try {
        data = text ? JSON.parse(text) : [];
      } catch {
        /* ignore parse errors */
      }
      if (!res.ok) {
        setHistoryError(
          extractErrorMessage(data, "Failed to load commit history")
        );
        return;
      }
      const list = Array.isArray(data)
        ? (data as Commit[])
        : Array.isArray((data as { commits?: Commit[] })?.commits)
        ? (data as { commits: Commit[] }).commits
        : [];
      setHistory(list);
    } catch {
      setHistoryError("Failed to connect to server");
    } finally {
      setHistoryLoading(false);
    }
  }

  function canReviewCommits(): boolean {
    const role = (repo?.role || "").toLowerCase();
    return role === "admin" || role === "reviewer";
  }

  function canAccessDrafts(): boolean {
    const role = (repo?.role || "").toLowerCase();
    return role === "admin" || role === "author";
  }

  function handleCommitButtonClick() {
    setCommitMode((v) => !v);
    setRenameMode(false);
    setDeleteMode(false);
  }

  async function handleDraftForCommit(draft: Draft) {
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }

    if (isDraftStale(draft, latestHeadHash)) {
      const draftId = draft.draft_id ?? draft.id;
      alert(
        "This draft is behind the latest accepted commit. Open it to review conflicts before submitting."
      );
      if (draftId) router.push(`/repo/${repoId}/draft/${draftId}`);
      return;
    }

    const summary = window.prompt(
      `Submit "${draft.label || "Draft"}" for review.\n\nCommit summary (1-200 chars):`,
      ""
    );
    if (!summary) return;
    const trimmed = summary.trim();
    if (!trimmed || trimmed.length > 200) {
      alert("Commit summary must be 1-200 characters.");
      return;
    }

    setSubmittingCommit(true);
    try {
      const draftId = draft.draft_id ?? draft.id;
      const res = await fetch(`/api/repos/${repoId}/commits`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ draft_id: draftId, commit_summary: trimmed }),
      });
      const text = await res.text();
      let data: any = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        /* ignore parse errors */
      }
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      if (!res.ok) {
        alert(extractErrorMessage(data, "Failed to submit draft for review"));
        return;
      }
      alert("Draft submitted for review!");
      setCommitMode(false);
      await loadCommits();
      await loadDrafts();
    } catch {
      alert("Failed to connect to server");
    } finally {
      setSubmittingCommit(false);
    }
  }

  async function handleApproveCommit(commitHash: string) {
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }

    if (!window.confirm("Approve this commit?")) return;

    setApprovingHash(commitHash);
    setOpenCommitMenu(null);
    try {
      const res = await fetch(
        `/api/repos/${repoId}/commits/${commitHash}/approve`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        }
      );
      const text = await res.text();
      let data: any = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        /* ignore parse errors */
      }
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      if (!res.ok) {
        alert(extractErrorMessage(data, "Failed to approve commit"));
        return;
      }
      alert("Commit approved!");
      await loadCommits();
      await loadHistory();
      await loadView();
    } catch {
      alert("Failed to connect to server");
    } finally {
      setApprovingHash(null);
    }
  }

  async function handleRejectCommit(commitHash: string) {
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }

    const comment = window.prompt(
      "Reject this commit?\n\nOptional comment (0-500 chars):",
      ""
    );
    if (comment === null) return;
    if (comment.length > 500) {
      alert("Comment must be 0-500 characters.");
      return;
    }

    setRejectingHash(commitHash);
    setOpenCommitMenu(null);
    try {
      const res = await fetch(
        `/api/repos/${repoId}/commits/${commitHash}/reject`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ comment: comment || undefined }),
        }
      );
      const text = await res.text();
      let data: any = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        /* ignore parse errors */
      }
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      if (!res.ok) {
        alert(extractErrorMessage(data, "Failed to reject commit"));
        return;
      }
      alert("Commit rejected!");
      await loadCommits();
      await loadHistory();
    } catch {
      alert("Failed to connect to server");
    } finally {
      setRejectingHash(null);
    }
  }

  async function handleDeleteRepo() {
    if (deletingRepo || !repoId) return;
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }
    const repoName = repo?.repo_name || "this repository";
    const confirmed = window.confirm(
      `Delete "${repoName}"?\n\nThis action cannot be undone. All drafts, commits, and files will be permanently removed.`
    );
    if (!confirmed) return;

    setDeletingRepo(true);
    try {
      const res = await fetch(`/api/repos/${repoId}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      if (!res.ok && res.status !== 204) {
        const text = await res.text();
        let data: unknown = {};
        if (text) {
          try {
            data = JSON.parse(text);
          } catch {
            /* ignore */
          }
        }
        alert(extractErrorMessage(data, "Failed to delete repository"));
        return;
      }
      router.push("/homepage");
    } catch {
      alert("Failed to connect to server");
    } finally {
      setDeletingRepo(false);
    }
  }

  async function handleNewDraft() {
    if (creatingDraft || !repoId) return;
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }

    const rawName = window.prompt("Name for the new draft:", "");
    if (rawName === null) return; // user cancelled
    const label = rawName.trim();
    if (!label) {
      alert("Draft name cannot be empty.");
      return;
    }
    if (label.length > 100) {
      alert("Draft name must be 100 characters or fewer.");
      return;
    }

    setCreatingDraft(true);
    try {
      const res = await fetch(`/api/repos/${repoId}/drafts`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ label }),
      });

      const text = await res.text();
      const data = text ? JSON.parse(text) : {};

      if (res.status === 401) {
        router.push("/login");
        return;
      }
      if (!res.ok) {
        alert(extractErrorMessage(data, "Failed to create draft"));
        return;
      }

      const draftId = data.draft_id ?? data.id;

      // Ensure the chosen name sticks: the spec updates labels via PATCH.
      // Only PATCH if the returned label doesn't already match what we asked for.
      if (draftId && data?.label !== label) {
        try {
          await fetch(`/api/repos/${repoId}/drafts/${draftId}`, {
            method: "PATCH",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${token}`,
            },
            body: JSON.stringify({ label }),
          });
        } catch {
          // Non-fatal: draft exists, just without the custom label.
        }
      }

      loadDrafts();

      if (draftId) {
        router.push(`/repo/${repoId}/draft/${draftId}`);
      } else {
        alert("Draft created but no draft_id was returned.");
      }
    } catch {
      alert("Failed to connect to server");
    } finally {
      setCreatingDraft(false);
    }
  }

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }
    setCurrentUserEmail(localStorage.getItem("email"));
    if (!repoId) return;
    if (!UUID_RE.test(repoId)) {
      setError(`Invalid repository ID: "${repoId}"`);
      setLoading(false);
      return;
    }

    fetch(`/api/repos/${repoId}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(async (res) => {
        if (res.status === 401) {
          router.push("/login");
          return null;
        }
        const data = await res.json();
        if (!res.ok) {
          setError(extractErrorMessage(data, "Failed to load repository"));
          return null;
        }
        return data as Repo;
      })
      .then((data) => {
        if (data) setRepo(data);
      })
      .catch(() => setError("Failed to connect to server"))
      .finally(() => setLoading(false));

    loadDrafts();
    loadCommits();
    loadHistory();
    loadView();
    loadRepoHead();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId, router]);

  useEffect(() => {
    if (!repoId || !UUID_RE.test(repoId)) return;

    // Spec: poll /head every ~30s with ±5s jitter, paused while the tab is
    // hidden, and re-fire immediately when the tab becomes visible. Jitter
    // spreads polling load across clients and avoids a thundering-herd
    // synchronisation with other tabs/users. We use setTimeout recursion
    // (not setInterval) so each tick can pick its own delay independently.
    let stopped = false;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;

    const jitteredDelay = () =>
      25_000 + Math.floor(Math.random() * 10_001); // 25–35s

    const schedule = (ms: number) => {
      if (stopped) return;
      timeoutId = setTimeout(tick, ms);
    };

    const tick = async () => {
      if (stopped) return;
      if (document.visibilityState !== "visible") {
        // Tab is hidden — don't burn requests; the visibility handler will
        // resume polling immediately when the user comes back.
        return;
      }
      controller?.abort();
      controller = new AbortController();
      try {
        await loadRepoHead(controller.signal);
      } finally {
        schedule(jitteredDelay());
      }
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        if (timeoutId) clearTimeout(timeoutId);
        void tick();
      }
    };

    // Kick off the first poll; subsequent ones are scheduled by `tick`.
    void tick();
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      stopped = true;
      if (timeoutId) clearTimeout(timeoutId);
      controller?.abort();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId, router]);

  useEffect(() => {
    if (isAdmin) {
      loadInvites();
      loadMembers();
    } else {
      setInvites([]);
      setMembers([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAdmin, repoId]);

  const files: RepoFile[] = repo?.files ?? [];

  const filteredFiles = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return files;
    return files.filter((f) => f.name.toLowerCase().includes(q));
  }, [files, search]);

  const filteredViewFiles = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return viewFiles;
    return viewFiles.filter((f) => f.path.toLowerCase().includes(q));
  }, [viewFiles, search]);

  // Build a folder tree from the flat list of view files.
  type TreeNode = {
    name: string;
    path: string;
    isFolder: boolean;
    file?: ViewFile;
    children: TreeNode[];
  };

  const viewTree = useMemo<TreeNode>(() => {
    const root: TreeNode = {
      name: "",
      path: "",
      isFolder: true,
      children: [],
    };
    const folderMap = new Map<string, TreeNode>();
    folderMap.set("", root);

    for (const f of filteredViewFiles) {
      const parts = f.path.split("/").filter(Boolean);
      let parentPath = "";
      let parent = root;
      for (let i = 0; i < parts.length - 1; i++) {
        const segment = parts[i];
        const currPath = parentPath ? `${parentPath}/${segment}` : segment;
        let folder = folderMap.get(currPath);
        if (!folder) {
          folder = {
            name: segment,
            path: currPath,
            isFolder: true,
            children: [],
          };
          folderMap.set(currPath, folder);
          parent.children.push(folder);
        }
        parent = folder;
        parentPath = currPath;
      }
      const fileName = parts[parts.length - 1] ?? f.path;
      parent.children.push({
        name: fileName,
        path: f.path,
        isFolder: false,
        file: f,
        children: [],
      });
    }

    // Sort: folders first, then alphabetical
    const sortTree = (node: TreeNode) => {
      node.children.sort((a, b) => {
        if (a.isFolder !== b.isFolder) return a.isFolder ? -1 : 1;
        return a.name.localeCompare(b.name);
      });
      node.children.forEach(sortTree);
    };
    sortTree(root);
    return root;
  }, [filteredViewFiles]);

  function renderTreeNodes(
    nodes: TreeNode[],
    depth: number,
    expanded: Set<string>,
    forceExpand: boolean,
    onToggle: (p: string) => void,
    onOpen: (f: ViewFile) => void,
    openingPath: string | null
  ): ReactNode[] {
    const out: ReactNode[] = [];
    for (const node of nodes) {
      const isExpanded = forceExpand || expanded.has(node.path);
      const indent = 12 + depth * 16;
      if (node.isFolder) {
        out.push(
          <li
            key={`dir:${node.path}`}
            style={styles.fileRow}
            onClick={() => onToggle(node.path)}
          >
            <span
              style={{
                ...styles.colName,
                paddingLeft: indent,
                cursor: "pointer",
                color: "#4fc3f7",
              }}
            >
              {isExpanded ? "▾" : "▸"} 📁 {node.name}
            </span>
            <span style={styles.colSize}>—</span>
            <span style={styles.colDate}>folder</span>
          </li>
        );
        if (isExpanded) {
          out.push(
            ...renderTreeNodes(
              node.children,
              depth + 1,
              expanded,
              forceExpand,
              onToggle,
              onOpen,
              openingPath
            )
          );
        }
      } else if (node.file) {
        const isOpening = openingPath === node.path;
        out.push(
          <li
            key={`file:${node.path}`}
            style={{
              ...styles.fileRow,
              cursor: "pointer",
              ...(isOpening ? { opacity: 0.6 } : {}),
            }}
            onClick={() => !isOpening && onOpen(node.file!)}
            title={`Open ${node.path}`}
          >
            <span style={{ ...styles.colName, paddingLeft: indent }}>
              📄 {node.name}
            </span>
            <span style={styles.colSize}>{formatSize(node.file.size)}</span>
            <span style={styles.colDate}>
              {isOpening ? "Opening…" : node.file.content_type}
            </span>
          </li>
        );
      }
    }
    return out;
  }

  return (
    <div style={styles.container}>
      {/* NAVBAR */}
      <div style={styles.navbar}>
        <Link href="/homepage" style={styles.brand}>
          <h3 style={{ margin: 0 }}>ChronoVS</h3>
        </Link>
        <div style={styles.avatar} />
      </div>

      <div style={styles.content}>
        {/* LEFT SIDEBAR — SEARCH + DRAFTS */}
        <aside style={styles.sidebar}>
          <label style={styles.sidebarLabel}>Search files</label>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Type a file name…"
            style={styles.searchInput}
          />
          <div style={styles.searchMeta}>
            {filteredFiles.length} / {files.length} files
          </div>

          {canAccessDrafts() && (
          <div style={styles.draftsSection}>
            <div style={styles.draftsHeader}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
                <button
                  onClick={() => setDraftsCollapsed((v) => !v)}
                  style={styles.collapseBtn}
                  title={draftsCollapsed ? "Expand drafts" : "Collapse drafts"}
                >
                  <span style={styles.collapseArrow}>
                    {draftsCollapsed ? "▸" : "▾"}
                  </span>
                  <label style={{ ...styles.sidebarLabel, cursor: "pointer" }}>
                    Drafts
                  </label>
                </button>
                <button
                  onClick={loadDrafts}
                  style={{ ...styles.refreshBtn, ...styles.iconBtn }}
                  title="Refresh drafts"
                >
                  ↻
                </button>
              </div>
              {!draftsCollapsed && (
              <div style={{ display: "flex", gap: 4 }}>
                <button
                  onClick={() => {
                    setRenameMode((v) => !v);
                    setDeleteMode(false);
                  }}
                  style={{
                    ...styles.refreshBtn,
                    ...styles.modeBtn,
                    ...(renameMode
                      ? { borderColor: PURPLE, color: "white" }
                      : {}),
                  }}
                  title={renameMode ? "Cancel rename mode" : "Rename a draft"}
                >
                  {renameMode ? "Cancel" : "Rename Draft"}
                </button>
                <button
                  onClick={() => {
                    setDeleteMode((v) => !v);
                    setRenameMode(false);
                  }}
                  style={{
                    ...styles.refreshBtn,
                    ...styles.modeBtn,
                    ...(deleteMode
                      ? {
                          borderColor: "#ff6b6b",
                          color: "#ff6b6b",
                        }
                      : {}),
                  }}
                  title={deleteMode ? "Cancel delete mode" : "Delete a draft"}
                >
                  {deleteMode ? "Cancel" : "Delete Draft"}
                </button>
                <button
                  onClick={handleCommitButtonClick}
                  disabled={submittingCommit}
                  style={{
                    ...styles.refreshBtn,
                    ...styles.modeBtn,
                    ...(commitMode
                      ? { borderColor: "#4fc3f7", color: "#4fc3f7" }
                      : {}),
                    ...(submittingCommit ? { opacity: 0.6, cursor: "wait" } : {}),
                  }}
                  title={commitMode ? "Cancel commit mode" : "Select a draft to commit"}
                >
                  {submittingCommit ? "Submitting…" : commitMode ? "Cancel" : "Commit"}
                </button>
              </div>
              )}
            </div>
            {!draftsCollapsed && (<>
            {renameMode && visibleDrafts.length > 0 && (
              <div style={styles.deleteHint}>
                Click a draft to rename it.
              </div>
            )}
            {deleteMode && visibleDrafts.length > 0 && (
              <div style={styles.deleteHint}>
                Click a draft to delete it.
              </div>
            )}
            {commitMode && visibleDrafts.length > 0 && (
              <div style={styles.deleteHint}>
                Click a draft to submit it for review.
              </div>
            )}
            {staleDrafts.length > 0 && (
              <div style={styles.staleBanner}>
                {staleDrafts.length === 1
                  ? "1 draft is behind HEAD."
                  : `${staleDrafts.length} drafts are behind HEAD.`}{" "}
                Open stale drafts to review conflicts.
              </div>
            )}

            {draftsLoading ? (
              <div style={styles.draftsState}>Loading…</div>
            ) : draftsError ? (
              <div style={{ ...styles.draftsState, color: "#ff6b6b" }}>
                {draftsError}
              </div>
            ) : visibleDrafts.length === 0 ? (
              <div style={styles.draftsState}>No drafts yet.</div>
            ) : (
              <ul style={styles.draftsList}>
                {visibleDrafts.map((d) => {
                  const id = d.draft_id ?? d.id ?? "";
                  if (!id) return null;
                  const title =
                    (d.label && d.label.trim()) ||
                    `Draft — ${formatDate(d.created_at)}`;
                  const isDeleting = deletingId === id;
                  const isRenaming = renamingId === id;
                  const isStale = isDraftStale(d, latestHeadHash);
                  const isConflictLoading = conflictLoadingDraftId === id;

                  const content = (
                    <>
                      <span style={styles.draftTitle}>
                        {isDeleting
                          ? "Deleting…"
                          : isRenaming
                          ? "Renaming…"
                          : title}
                      </span>
                      <span style={styles.draftMeta}>
                        {d.status && (
                          <span style={styles.draftStatus}>{d.status}</span>
                        )}
                        {isStale && (
                          <span style={styles.staleStatus}>stale</span>
                        )}
                        <span style={styles.draftDate}>
                          {formatDate(d.updated_at || d.created_at)}
                        </span>
                      </span>
                    </>
                  );

                  return (
                    <li key={id}>
                      {deleteMode ? (
                        <button
                          onClick={() => handleDeleteDraft(id, title)}
                          disabled={isDeleting}
                          style={{
                            ...styles.draftItem,
                            ...styles.draftItemDelete,
                            ...(isDeleting
                              ? { opacity: 0.6, cursor: "wait" }
                              : {}),
                          }}
                        >
                          {content}
                        </button>
                      ) : renameMode ? (
                        <button
                          onClick={() => handleRenameDraft(id, title)}
                          disabled={isRenaming}
                          style={{
                            ...styles.draftItem,
                            ...styles.draftItemRename,
                            ...(isRenaming
                              ? { opacity: 0.6, cursor: "wait" }
                              : {}),
                          }}
                        >
                          {content}
                        </button>
                      ) : commitMode ? (
                        <button
                          onClick={() => handleDraftForCommit(d)}
                          disabled={submittingCommit}
                          style={{
                            ...styles.draftItem,
                            ...styles.draftItemCommit,
                            ...(isStale ? styles.draftItemStale : {}),
                            ...(submittingCommit
                              ? { opacity: 0.6, cursor: "wait" }
                              : {}),
                          }}
                        >
                          {content}
                        </button>
                      ) : (
                        isStale ? (
                          <button
                            onClick={() => handleOpenConflictReview(d, title)}
                            disabled={isConflictLoading}
                            style={{
                              ...styles.draftItem,
                              ...styles.draftConflictButton,
                              ...styles.draftItemStale,
                              ...(isConflictLoading
                                ? { opacity: 0.6, cursor: "wait" }
                                : {}),
                            }}
                            title="Open Conflict Review"
                          >
                            {content}
                            <span style={styles.conflictReviewCta}>
                              {isConflictLoading
                                ? "Loading review..."
                                : "Review conflicts"}
                            </span>
                          </button>
                        ) : (
                          <Link
                            href={`/repo/${repoId}/draft/${id}`}
                            style={styles.draftItem}
                          >
                            {content}
                          </Link>
                        )
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
            </>)}
          </div>
          )}

          {/* PENDING COMMITS SECTION — Only for admin/reviewer */}
          {canReviewCommits() && (
            <div style={styles.pendingSection}>
              <div style={styles.pendingHeader}>
                <button
                  onClick={() => setPendingCollapsed((v) => !v)}
                  style={styles.collapseBtn}
                  title={pendingCollapsed ? "Expand pending" : "Collapse pending"}
                >
                  <span style={styles.collapseArrow}>
                    {pendingCollapsed ? "▸" : "▾"}
                  </span>
                  <label style={{ ...styles.sidebarLabel, cursor: "pointer" }}>
                    Pending
                  </label>
                </button>
                <button
                  onClick={loadCommits}
                  style={{ ...styles.refreshBtn, ...styles.iconBtn }}
                  title="Refresh pending commits"
                >
                  ↻
                </button>
              </div>

              {!pendingCollapsed && (<>
              {commitsLoading ? (
                <div style={styles.commitsState}>Loading…</div>
              ) : commitsError ? (
                <div style={{ ...styles.commitsState, color: "#ff6b6b" }}>
                  {commitsError}
                </div>
              ) : commits.length === 0 ? (
                <div style={styles.commitsState}>No pending commits.</div>
              ) : (
                <ul style={styles.commitsList}>
                  {commits.map((commit) => {
                    const isApproving = approvingHash === commit.commit_hash;
                    const isRejecting = rejectingHash === commit.commit_hash;
                    const menuOpen = openCommitMenu === commit.commit_hash;
                    const statusInfo = commitStatuses[commit.commit_hash];
                    const visibleStatus = statusInfo?.status || commit.status;
                    const statusColor =
                      visibleStatus === "approved"
                        ? "#51cf66"
                        : visibleStatus === "rejected"
                        ? "#ff6b6b"
                        : visibleStatus === "sibling_rejected"
                        ? "#ffa94d"
                        : visibleStatus === "cancelled"
                        ? MUTED
                        : "#4fc3f7";
                    return (
                      <li key={commit.commit_hash} style={styles.commitItem}>
                        {commit.draft_id ? (
                          <Link
                            href={`/repo/${repoId}/draft/${commit.draft_id}`}
                            style={styles.commitContentLink}
                            title="Click to open commit"
                          >
                            <span style={styles.commitSummary}>{commit.commit_summary}</span>
                            <span style={styles.commitMeta}>
                              <span
                                style={{
                                  ...styles.commitStatus,
                                  color: statusColor,
                                  borderColor: statusColor,
                                }}
                              >
                                {visibleStatus}
                              </span>
                              <span style={styles.commitOwner}>{commit.owner_id.substring(0, 8)}</span>
                              <span style={styles.commitDate}>{formatDateTime(commit.timestamp)}</span>
                              <span style={styles.commitHashSmall}>
                                #{commit.commit_hash.substring(0, 7)}
                              </span>
                            </span>
                          </Link>
                        ) : (
                          <div style={styles.commitContent}>
                            <span style={styles.commitSummary}>{commit.commit_summary}</span>
                            <span style={styles.commitMeta}>
                              <span
                                style={{
                                  ...styles.commitStatus,
                                  color: statusColor,
                                  borderColor: statusColor,
                                }}
                              >
                                {visibleStatus}
                              </span>
                              <span style={styles.commitOwner}>{commit.owner_id.substring(0, 8)}</span>
                              <span style={styles.commitDate}>{formatDateTime(commit.timestamp)}</span>
                            </span>
                            {statusInfo?.reviewer_comment && (
                              <span style={styles.commitReviewerComment}>
                                {statusInfo.reviewer_comment}
                              </span>
                            )}
                          </div>
                        )}
                        <div style={styles.commitActions}>
                          <button
                            onClick={() => refreshCommitStatus(commit.commit_hash)}
                            disabled={statusLoadingHash === commit.commit_hash}
                            style={{
                              ...styles.statusPollButton,
                              ...(statusLoadingHash === commit.commit_hash
                                ? { opacity: 0.6, cursor: "wait" }
                                : {}),
                            }}
                            title="Check commit status"
                          >
                            {statusLoadingHash === commit.commit_hash
                              ? "..."
                              : "Status"}
                          </button>
                          <button
                            onClick={() =>
                              setOpenCommitMenu((cur) =>
                                cur === commit.commit_hash ? null : commit.commit_hash
                              )
                            }
                            style={styles.dotsBtn}
                            aria-label="More actions"
                            title="More actions"
                            disabled={isApproving || isRejecting}
                          >
                            ⋮
                          </button>
                          {menuOpen && (
                            <div style={styles.commitMenu}>
                              <button
                                onClick={() => handleApproveCommit(commit.commit_hash)}
                                disabled={isApproving}
                                style={{
                                  ...styles.menuItem,
                                  ...styles.menuItemSuccess,
                                  ...(isApproving ? { opacity: 0.6, cursor: "wait" } : {}),
                                }}
                              >
                                {isApproving ? "Approving…" : "Approve"}
                              </button>
                              <button
                                onClick={() => handleRejectCommit(commit.commit_hash)}
                                disabled={isRejecting}
                                style={{
                                  ...styles.menuItem,
                                  ...styles.menuItemDanger,
                                  ...(isRejecting ? { opacity: 0.6, cursor: "wait" } : {}),
                                }}
                              >
                                {isRejecting ? "Rejecting…" : "Reject"}
                              </button>
                            </div>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
              </>)}
            </div>
          )}

          {/* COMMIT HISTORY SECTION — Any repo member */}
          <div style={styles.pendingSection}>
            <div style={styles.pendingHeader}>
              <button
                onClick={() => setHistoryCollapsed((v) => !v)}
                style={styles.collapseBtn}
                title={historyCollapsed ? "Expand history" : "Collapse history"}
              >
                <span style={styles.collapseArrow}>
                  {historyCollapsed ? "▸" : "▾"}
                </span>
                <label style={{ ...styles.sidebarLabel, cursor: "pointer" }}>
                  Commit History
                </label>
              </button>
              <button
                onClick={loadHistory}
                style={{ ...styles.refreshBtn, ...styles.iconBtn }}
                title="Refresh commit history"
              >
                ↻
              </button>
            </div>

            {!historyCollapsed && (<>
            {historyLoading ? (
              <div style={styles.commitsState}>Loading…</div>
            ) : historyError ? (
              <div style={{ ...styles.commitsState, color: "#ff6b6b" }}>
                {historyError}
              </div>
            ) : history.length === 0 ? (
              <div style={styles.commitsState}>No commit history.</div>
            ) : (
              <ul style={styles.commitsList}>
                {history.map((commit) => {
                  const statusColor =
                    commit.status === "approved"
                      ? "#51cf66"
                      : commit.status === "rejected"
                      ? "#ff6b6b"
                      : commit.status === "sibling_rejected"
                      ? "#ffa94d"
                      : MUTED;
                  const title = commit.reviewer_comment
                    ? `${commit.status.toUpperCase()} — ${commit.reviewer_comment}`
                    : `${commit.status.toUpperCase()} — click to open`;
                  const content = (
                    <>
                      <span style={styles.commitSummary}>
                        {commit.commit_summary}
                      </span>
                      <span style={styles.commitMeta}>
                        <span
                          style={{
                            ...styles.commitStatus,
                            color: statusColor,
                            borderColor: statusColor,
                          }}
                        >
                          {commit.status}
                        </span>
                        <span style={styles.commitOwner}>
                          {commit.owner_id.substring(0, 8)}
                        </span>
                        <span style={styles.commitDate}>
                          {formatDateTime(commit.timestamp)}
                        </span>
                        <span style={styles.commitHashSmall}>
                          #{commit.commit_hash.substring(0, 7)}
                        </span>
                      </span>
                    </>
                  );
                  return (
                    <li key={commit.commit_hash} style={styles.commitItem}>
                      <button
                        onClick={() => handleViewCommit(commit)}
                        style={{
                          ...styles.commitContentLink,
                          ...(viewCommitHash === commit.commit_hash
                            ? styles.commitContentSelected
                            : {}),
                        }}
                        title={title}
                      >
                        {content}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
            </>)}
          </div>
        </aside>

        {/* MAIN */}
        <main style={styles.main}>
          {loading ? (
            <div style={styles.stateText}>Loading repository…</div>
          ) : error ? (
            <div style={{ ...styles.stateText, color: "#ff6b6b" }}>{error}</div>
          ) : repo ? (
            <>
              {/* METADATA HEADER */}
              <section style={styles.metaHeader}>
                <div style={styles.metaTitleRow}>
                  <h1 style={styles.repoTitle}>{repo.repo_name}</h1>
                  {repo.visibility && (
                    <span style={styles.badge}>{repo.visibility}</span>
                  )}
                </div>
                {repo.description && (
                  <p style={styles.description}>{repo.description}</p>
                )}

                <dl style={styles.metaGrid}>
                  <div style={styles.metaItem}>
                    <dt style={styles.metaKey}>Owner</dt>
                    <dd style={styles.metaVal}>{getOwnerDisplay(repo)}</dd>
                  </div>
                  <div style={styles.metaItem}>
                    <dt style={styles.metaKey}>Created At</dt>
                    <dd style={styles.metaVal}>{formatDateTime(repo.created_at)}</dd>
                  </div>
                  <div style={styles.metaItem}>
                    <dt style={styles.metaKey}>Last updated</dt>
                    <dd style={styles.metaVal}>
                      {formatDateTime(repoHead?.commit_timestamp ?? repo.updated_at)}
                    </dd>
                  </div>
                  <div style={styles.metaItem}>
                    <dt style={styles.metaKey}>HEAD</dt>
                    <dd style={styles.metaVal}>
                      {repoHead?.latest_commit_hash
                        ? `#${repoHead.latest_commit_hash.substring(0, 8)}`
                        : "No commits"}
                    </dd>
                  </div>
                </dl>
              </section>

              {/* FILES SECTION */}
              <div style={styles.filesWrapper}>
                <div style={styles.filesToolbar}>
                  {actionsForRole(repo.role, {
                    onNewDraft: handleNewDraft,
                    onInviteMember: handleInviteMember,
                    inviting,
                    onDeleteRepo: handleDeleteRepo,
                    deletingRepo,
                  }).map((action) => {
                    const isNewDraft = action.label === "+ New Draft";
                    const isDeleting = action.label === "Deleting...";
                    const disabled = (isNewDraft && creatingDraft) || isDeleting;
                    return (
                      <button
                        key={action.label}
                        onClick={action.onClick}
                        disabled={disabled}
                        style={{
                          ...styles.toolbarButton,
                          ...(action.variant === "primary"
                            ? styles.btnPrimary
                            : action.variant === "danger"
                            ? styles.btnDanger
                            : styles.btnSecondary),
                          ...(disabled ? { opacity: 0.6, cursor: "wait" } : {}),
                        }}
                      >
                        {disabled ? "Creating…" : action.label}
                      </button>
                    );
                  })}
                  {repo.role && (
                    <span style={styles.roleTag}>
                      Role: {String(repo.role).toLowerCase()}
                    </span>
                  )}
                </div>

                <div style={styles.filesRect}>
                  <div style={styles.viewBanner}>
                    <span style={styles.viewBannerLabel}>
                      {selectedCommit
                        ? selectedCommit.commit_summary
                        : viewCommitHash
                        ? "Latest accepted commit"
                        : "Committed files"}
                    </span>
                    {viewCommitHash && (
                      <span style={styles.viewBannerHash}>
                        #{viewCommitHash.substring(0, 10)}
                      </span>
                    )}
                    {selectedCommit &&
                      (() => {
                        const s = (selectedCommit.status || "").toLowerCase();
                        if (s === "approved") return null;
                        const color =
                          s === "rejected" || s === "sibling_rejected"
                            ? "#ff6b6b"
                            : MUTED;
                        return (
                          <span
                            style={{
                              ...styles.viewBannerHash,
                              color,
                              borderColor: color,
                            }}
                            title={
                              selectedCommit.reviewer_comment
                                ? `Reviewer: ${selectedCommit.reviewer_comment}`
                                : undefined
                            }
                          >
                            {selectedCommit.status}
                          </span>
                        );
                      })()}
                    {selectedCommit && (
                      <button
                        onClick={() => loadView()}
                        style={{ ...styles.refreshBtn, ...styles.smallTextBtn }}
                        title="Back to latest accepted commit"
                      >
                        Latest
                      </button>
                    )}
                    <button
                      onClick={() =>
                        selectedCommit
                          ? loadView(selectedCommit.commit_hash, selectedCommit)
                          : loadView()
                      }
                      style={{ ...styles.refreshBtn, ...styles.iconBtn }}
                      title="Refresh committed files"
                    >
                      ↻
                    </button>
                  </div>

                  {viewLoading ? (
                    <div style={styles.emptyFiles}>Loading committed files…</div>
                  ) : viewError ? (
                    <div style={{ ...styles.emptyFiles, color: "#ff6b6b" }}>
                      {viewError}
                    </div>
                  ) : !viewCommitHash ? (
                    <div style={styles.emptyFiles}>
                      No accepted commits yet. Files shown here once a commit is approved.
                    </div>
                  ) : filteredViewFiles.length === 0 ? (
                    <div style={styles.emptyFiles}>
                      {viewFiles.length === 0
                        ? "No files in this commit."
                        : "No files match your search."}
                    </div>
                  ) : (
                    <ul style={styles.fileList}>
                      <li style={styles.fileHeaderRow}>
                        <span style={styles.colName}>Name</span>
                        <span style={styles.colSize}>Size</span>
                        <span style={styles.colDate}>Type</span>
                      </li>
                      {renderTreeNodes(
                        viewTree.children,
                        0,
                        expandedFolders,
                        search.trim().length > 0,
                        toggleFolder,
                        handleOpenViewFile,
                        openingFilePath
                      )}
                    </ul>
                  )}
                </div>
              </div>
            </>
          ) : null}
        </main>

        {isAdmin && (
          <aside style={{ ...styles.sidebar, ...styles.rightSidebar }}>
            <div style={styles.rightSectionHeader}>
              <label style={styles.sidebarLabel}>Invites</label>
              <div style={styles.headerActions}>
                {invitesOpen && (
                  <button
                    onClick={loadInvites}
                    style={{ ...styles.refreshBtn, ...styles.smallTextBtn }}
                    title="Refresh invites"
                  >
                    Reload
                  </button>
                )}
                <button
                  onClick={() => setInvitesOpen((v) => !v)}
                  style={{ ...styles.refreshBtn, ...styles.smallTextBtn }}
                  title={invitesOpen ? "Hide invites" : "Show invites"}
                >
                  {invitesOpen ? "Hide" : "Show"}
                </button>
              </div>
            </div>

            {invitesOpen && (
              <div style={styles.rightPanel}>
                {invitesLoading ? (
                  <div style={styles.draftsState}>Loading...</div>
                ) : invitesError ? (
                  <div style={{ ...styles.draftsState, color: "#ff6b6b" }}>
                    {invitesError}
                  </div>
                ) : invites.length === 0 ? (
                  <div style={styles.draftsState}>No pending invites.</div>
                ) : (
                  <ul style={styles.draftsList}>
                    {invites.map((invite) => (
                      <li key={invite.token_id} style={styles.inviteItem}>
                        <span style={styles.draftTitle}>{invite.invited_email}</span>
                        <span style={styles.draftMeta}>
                          <span style={styles.draftStatus}>{invite.role}</span>
                          <span style={styles.draftDate}>
                            Expires {formatDate(invite.expires_at)}
                          </span>
                        </span>
                        <div style={styles.inviteActions}>
                          {normalizedCurrentUserEmail ===
                            invite.invited_email.trim().toLowerCase() && (
                            <button
                              onClick={() => handleAcceptInvite(invite)}
                              disabled={
                                acceptingInviteId === invite.token_id ||
                                resendingInviteId === invite.token_id ||
                                revokingInviteId === invite.token_id
                              }
                              style={{
                                ...styles.inviteActionButton,
                                ...styles.inviteAcceptButton,
                                ...(acceptingInviteId === invite.token_id
                                  ? { opacity: 0.6, cursor: "wait" }
                                  : {}),
                              }}
                            >
                              {acceptingInviteId === invite.token_id
                                ? "Accepting..."
                                : "Accept"}
                            </button>
                          )}
                          <button
                            onClick={() => handleResendInvite(invite)}
                            disabled={
                              acceptingInviteId === invite.token_id ||
                              resendingInviteId === invite.token_id ||
                              revokingInviteId === invite.token_id
                            }
                            style={{
                              ...styles.inviteActionButton,
                              ...(resendingInviteId === invite.token_id
                                ? { opacity: 0.6, cursor: "wait" }
                                : {}),
                            }}
                          >
                            {resendingInviteId === invite.token_id
                              ? "Resending..."
                              : "Resend"}
                          </button>
                          <button
                            onClick={() => handleRevokeInvite(invite)}
                            disabled={
                              acceptingInviteId === invite.token_id ||
                              resendingInviteId === invite.token_id ||
                              revokingInviteId === invite.token_id
                            }
                            style={{
                              ...styles.inviteActionButton,
                              ...styles.inviteDangerButton,
                              ...(revokingInviteId === invite.token_id
                                ? { opacity: 0.6, cursor: "wait" }
                                : {}),
                            }}
                          >
                            {revokingInviteId === invite.token_id
                              ? "Revoking..."
                              : "Revoke"}
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            <div style={styles.membersSection}>
              <div style={styles.rightSectionHeader}>
                <label style={styles.sidebarLabel}>Members</label>
                <div style={styles.headerActions}>
                  {membersOpen && (
                    <button
                      onClick={loadMembers}
                      style={{ ...styles.refreshBtn, ...styles.smallTextBtn }}
                      title="Refresh members"
                    >
                      Reload
                    </button>
                  )}
                  <button
                    onClick={() => setMembersOpen((v) => !v)}
                    style={{ ...styles.refreshBtn, ...styles.smallTextBtn }}
                    title={membersOpen ? "Hide members" : "Show members"}
                  >
                    {membersOpen ? "Hide" : "Show"}
                  </button>
                </div>
              </div>

              {membersOpen && (
                <div style={styles.rightPanel}>
                  {membersLoading ? (
                    <div style={styles.draftsState}>Loading...</div>
                  ) : membersError ? (
                    <div style={{ ...styles.draftsState, color: "#ff6b6b" }}>
                      {membersError}
                    </div>
                  ) : members.length === 0 ? (
                    <div style={styles.draftsState}>No members yet.</div>
                  ) : (
                    <ul style={styles.draftsList}>
                      {members.map((member) => (
                        <li key={member.user_id} style={styles.inviteItem}>
                          <div style={styles.memberTitleRow}>
                            <span style={styles.draftTitle}>
                              {member.email || member.user_id}
                            </span>
                            <button
                              onClick={() => handleRemoveMember(member)}
                              disabled={removingMemberId === member.user_id}
                              style={{
                                ...styles.memberRemoveButton,
                                ...(removingMemberId === member.user_id
                                  ? { opacity: 0.6, cursor: "wait" }
                                  : {}),
                              }}
                            >
                              {removingMemberId === member.user_id
                                ? "Removing..."
                                : "Remove"}
                            </button>
                          </div>
                          <span style={styles.draftMeta}>
                            <button
                              onClick={() => handleChangeMemberRole(member)}
                              disabled={changingRoleUserId === member.user_id}
                              style={{
                                ...styles.roleButton,
                                ...(changingRoleUserId === member.user_id
                                  ? { opacity: 0.6, cursor: "wait" }
                                  : {}),
                              }}
                              title="Change member role"
                            >
                              {changingRoleUserId === member.user_id
                                ? "Changing..."
                                : member.role}
                            </button>
                            <span style={styles.draftDate}>
                              Joined {formatDate(member.joined_at)}
                            </span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          </aside>
        )}
      </div>

      {/* FILE VIEWER MODAL */}
      {openedFile && (
        <div
          style={styles.viewerOverlay}
          onClick={() => setOpenedFile(null)}
        >
          <div
            style={styles.viewerContent}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={styles.viewerHeader}>
              <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0, flex: 1 }}>
                <span style={styles.viewerTitle}>{openedFile.path}</span>
                <span style={styles.viewerSubtitle}>
                  {openedFile.content_type} · {formatSize(openedFile.size)}
                </span>
              </div>
              <a
                href={openedFile.url}
                target="_blank"
                rel="noopener noreferrer"
                style={styles.viewerDownload}
              >
                Open / Download ↗
              </a>
              <button
                onClick={() => setOpenedFile(null)}
                style={styles.viewerCloseBtn}
                title="Close"
              >
                ✕
              </button>
            </div>
            <div style={styles.viewerBody}>
              {openedFile.loadingText ? (
                <div style={styles.viewerState}>Loading file content…</div>
              ) : openedFile.text !== null ? (
                <pre style={styles.viewerPre}>{openedFile.text}</pre>
              ) : openedFile.content_type.startsWith("image/") ? (
                <img
                  src={openedFile.url}
                  alt={openedFile.path}
                  style={styles.viewerImage}
                />
              ) : openedFile.content_type.startsWith("video/") ? (
                <video
                  src={openedFile.url}
                  controls
                  style={styles.viewerImage}
                />
              ) : openedFile.content_type.startsWith("audio/") ? (
                <audio src={openedFile.url} controls style={{ width: "100%" }} />
              ) : openedFile.content_type === "application/pdf" ? (
                <iframe
                  src={openedFile.url}
                  style={styles.viewerIframe}
                  title={openedFile.path}
                />
              ) : (
                <div style={styles.viewerState}>
                  Preview not available for this file type.
                  <br />
                  Use the &quot;Open / Download&quot; link above.
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {conflictReview && (
        <div
          style={styles.viewerOverlay}
          onClick={() => setConflictReview(null)}
        >
          <div
            style={{ ...styles.viewerContent, ...styles.conflictModalContent }}
            onClick={(e) => e.stopPropagation()}
          >
            <div style={styles.viewerHeader}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={styles.viewerTitle}>
                  Conflict Review: {conflictReview.draftTitle}
                </div>
                <div style={styles.viewerSubtitle}>
                  {conflictReview.mode} against #
                  {conflictReview.pinnedHead.substring(0, 8)}
                  {conflictReview.baseCommitHash
                    ? `, base #${conflictReview.baseCommitHash.substring(0, 8)}`
                    : ", no base commit"}
                </div>
              </div>
              <Link
                href={`/repo/${repoId}/draft/${conflictReview.draftId}`}
                style={styles.viewerDownload}
              >
                Open Draft
              </Link>
              <button
                onClick={handleFinalizeRebase}
                disabled={
                  rebasingDraftId === conflictReview.draftId ||
                  requiredConflictPaths(
                    conflictReview.files,
                    conflictReview.collisionRoots
                  ).some((path) => !conflictReview.resolutions[path])
                }
                style={{
                  ...styles.viewerDownload,
                  ...styles.finalizeRebaseButton,
                  ...(rebasingDraftId === conflictReview.draftId
                    ? { opacity: 0.6, cursor: "wait" }
                    : {}),
                }}
                title="Finalize rebase"
              >
                {rebasingDraftId === conflictReview.draftId
                  ? "Rebasing..."
                  : "Finalize"}
              </button>
              <button
                onClick={() => setConflictReview(null)}
                style={styles.viewerCloseBtn}
                title="Close conflict review"
              >
                x
              </button>
            </div>
            <div style={styles.viewerBody}>
              {conflictError && (
                <div style={{ ...styles.draftsState, color: "#ff6b6b" }}>
                  {conflictError}
                </div>
              )}
              <div style={styles.conflictSummaryRow}>
                <span>{conflictReview.files.length} files classified</span>
                <span>
                  {
                    conflictReview.files.filter((f) =>
                      isConflictActionRequired(f, conflictReview.collisionRoots)
                    ).length
                  }{" "}
                  need review
                </span>
              </div>
              {conflictReview.files.length === 0 ? (
                <div style={styles.viewerState}>
                  No file differences were returned for this draft.
                </div>
              ) : (
                <ul style={styles.conflictList}>
                  {conflictReview.files.map((file) => {
                    const color = conflictCategoryColor(file.category);
                    const actionRequired = isConflictActionRequired(
                      file,
                      conflictReview.collisionRoots
                    );
                    const collisionRoot =
                      file.category === "type_collision"
                        ? findCollisionRootFor(
                            file.path,
                            conflictReview.collisionRoots
                          )
                        : null;
                    const isCollisionSibling =
                      file.category === "type_collision" &&
                      collisionRoot !== null &&
                      collisionRoot !== file.path;
                    const isCollisionRoot =
                      file.category === "type_collision" &&
                      collisionRoot === file.path;
                    const currentChoice =
                      conflictReview.resolutions[file.path];
                    return (
                      <li key={file.path} style={styles.conflictItem}>
                        <div style={styles.conflictFileTop}>
                          <span style={styles.conflictPath}>{file.path}</span>
                          <span
                            style={{
                              ...styles.conflictCategory,
                              color,
                              borderColor: color,
                            }}
                          >
                            {file.category}
                          </span>
                        </div>
                        <div style={styles.conflictHashes}>
                          <span>base {file.base_hash?.substring(0, 8) ?? "-"}</span>
                          <span>head {file.head_hash?.substring(0, 8) ?? "-"}</span>
                          <span>draft {file.draft_hash?.substring(0, 8) ?? "-"}</span>
                          {file.has_draft_changes && (
                            <span style={{ color: "#ffa94d" }}>draft changed</span>
                          )}
                          {isCollisionSibling && collisionRoot && (
                            <span style={{ color: MUTED }}>
                              resolved via root: {collisionRoot}
                            </span>
                          )}
                        </div>
                        {actionRequired && (
                          <div style={styles.resolutionControls}>
                            <button
                              onClick={() =>
                                setConflictResolution(file.path, "keep_mine")
                              }
                              style={{
                                ...styles.resolutionButton,
                                ...(currentChoice === "keep_mine"
                                  ? styles.resolutionButtonActive
                                  : {}),
                              }}
                            >
                              Keep mine
                            </button>
                            <button
                              onClick={() =>
                                setConflictResolution(file.path, "use_theirs")
                              }
                              style={{
                                ...styles.resolutionButton,
                                ...(currentChoice === "use_theirs"
                                  ? styles.resolutionButtonActive
                                  : {}),
                              }}
                            >
                              Use theirs
                            </button>
                          </div>
                        )}
                        {actionRequired &&
                          isCollisionRoot &&
                          currentChoice === "use_theirs" && (
                            <div style={styles.resolutionControls}>
                              <input
                                type="text"
                                placeholder="Optional: save my version as… (new path)"
                                value={conflictReview.saveAs[file.path] || ""}
                                onChange={(e) =>
                                  setConflictSaveAs(file.path, e.target.value)
                                }
                                style={styles.searchInput}
                              />
                            </div>
                          )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const PURPLE = "purple";
const BG = "#0d1117";
const PANEL = "#161b22";
const BORDER = "#30363d";
const TEXT = "#c9d1d9";
const MUTED = "#8b949e";

const styles: { [key: string]: CSSProperties } = {
  container: {
    minHeight: "100vh",
    background: BG,
    color: TEXT,
    fontFamily: "Arial",
    display: "flex",
    flexDirection: "column",
  },
  navbar: {
    height: 60,
    background: PURPLE,
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "0 20px",
    color: "white",
  },
  brand: {
    color: "white",
    textDecoration: "none",
  },
  avatar: {
    width: 30,
    height: 30,
    borderRadius: "50%",
    background: "#ccc",
  },
  content: {
    display: "flex",
    flex: 1,
  },
  sidebar: {
    width: 268,
    borderRight: `1px solid ${PURPLE}`,
    padding: 20,
    display: "flex",
    flexDirection: "column",
    gap: 10,
    boxSizing: "border-box",
  },
  rightSidebar: {
    borderRight: "none",
    borderLeft: `1px solid ${PURPLE}`,
  },
  sidebarLabel: {
    fontSize: 12,
    color: MUTED,
    textTransform: "uppercase",
    letterSpacing: 1,
  },
  collapseBtn: {
    background: "transparent",
    border: "none",
    padding: 0,
    display: "flex",
    alignItems: "center",
    gap: 6,
    cursor: "pointer",
    color: MUTED,
  },
  collapseArrow: {
    fontSize: 10,
    color: MUTED,
    width: 12,
    display: "inline-block",
    textAlign: "center" as const,
  },
  searchInput: {
    padding: "8px 10px",
    borderRadius: 6,
    border: `1px solid ${BORDER}`,
    background: PANEL,
    color: TEXT,
    outline: "none",
  },
  searchMeta: {
    fontSize: 12,
    color: MUTED,
  },
  main: {
    flex: 1,
    padding: "30px 40px",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
  },
  stateText: {
    color: MUTED,
    marginTop: 40,
  },
  metaHeader: {
    width: "100%",
    maxWidth: 1100,
    marginBottom: 30,
    paddingBottom: 20,
    borderBottom: `1px solid ${BORDER}`,
  },
  metaTitleRow: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    marginBottom: 8,
  },
  repoTitle: {
    margin: 0,
    fontSize: 26,
    color: "white",
  },
  badge: {
    fontSize: 11,
    padding: "2px 8px",
    borderRadius: 10,
    border: `1px solid ${PURPLE}`,
    color: PURPLE,
    textTransform: "uppercase",
    letterSpacing: 1,
  },
  description: {
    margin: "6px 0 20px",
    color: MUTED,
    fontSize: 14,
    lineHeight: 1.5,
  },
  metaGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
    gap: 16,
    margin: 0,
  },
  metaItem: {
    background: PANEL,
    border: `1px solid ${BORDER}`,
    borderRadius: 8,
    padding: "10px 14px",
  },
  metaKey: {
    fontSize: 11,
    color: MUTED,
    textTransform: "uppercase",
    letterSpacing: 1,
    marginBottom: 4,
  },
  metaVal: {
    margin: 0,
    color: TEXT,
    fontSize: 14,
  },
  filesWrapper: {
    width: "45vw",
    display: "flex",
    flexDirection: "column",
  },
  filesToolbar: {
    display: "flex",
    flexWrap: "wrap",
    alignItems: "center",
    gap: 8,
    width: "45vw",
    marginBottom: 10,
  },
  toolbarButton: {
    padding: "8px 14px",
    borderRadius: 6,
    cursor: "pointer",
    fontWeight: 600,
    fontSize: 13,
    transition: "opacity 0.15s",
  },
  btnPrimary: {
    background: PURPLE,
    color: "white",
    border: `1px solid ${PURPLE}`,
  },
  btnSecondary: {
    background: "transparent",
    color: TEXT,
    border: `1px solid ${BORDER}`,
  },
  btnDanger: {
    background: "transparent",
    color: "#ff6b6b",
    border: "1px solid #ff6b6b",
    marginLeft: "auto",
  },
  roleTag: {
    marginLeft: "auto",
    fontSize: 11,
    color: MUTED,
    textTransform: "uppercase",
    letterSpacing: 1,
  },
  filesRect: {
    width: "45vw",
    height: "45vh",
    background: PANEL,
    border: `1px solid ${PURPLE}`,
    borderRadius: 10,
    overflowY: "auto",
    padding: 10,
  },
  viewBanner: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "6px 8px",
    marginBottom: 8,
    borderBottom: `1px solid ${BORDER}`,
  },
  viewBannerLabel: {
    fontSize: 11,
    color: MUTED,
    textTransform: "uppercase" as const,
    letterSpacing: 1,
  },
  viewBannerHash: {
    fontFamily: "monospace",
    fontSize: 12,
    color: "#4fc3f7",
    flex: 1,
  },
  viewerOverlay: {
    position: "fixed" as const,
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    background: "rgba(0, 0, 0, 0.75)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 2000,
    padding: 24,
  },
  viewerContent: {
    background: PANEL,
    border: `1px solid ${PURPLE}`,
    borderRadius: 10,
    width: "90vw",
    maxWidth: 1100,
    height: "85vh",
    display: "flex",
    flexDirection: "column" as const,
    overflow: "hidden",
    boxShadow: "0 20px 60px rgba(0, 0, 0, 0.7)",
  },
  viewerHeader: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "12px 16px",
    borderBottom: `1px solid ${BORDER}`,
  },
  viewerTitle: {
    fontSize: 14,
    fontWeight: 600,
    color: TEXT,
    whiteSpace: "nowrap" as const,
    overflow: "hidden",
    textOverflow: "ellipsis",
  },
  viewerSubtitle: {
    fontSize: 11,
    color: MUTED,
  },
  viewerDownload: {
    fontSize: 12,
    color: "#4fc3f7",
    textDecoration: "none",
    padding: "6px 10px",
    border: `1px solid ${BORDER}`,
    borderRadius: 6,
    whiteSpace: "nowrap" as const,
  },
  finalizeRebaseButton: {
    background: PURPLE,
    color: "white",
    borderColor: PURPLE,
    cursor: "pointer",
  },
  viewerCloseBtn: {
    background: "transparent",
    border: "none",
    color: TEXT,
    fontSize: 18,
    cursor: "pointer",
    padding: "4px 8px",
  },
  viewerBody: {
    flex: 1,
    overflow: "auto",
    padding: 16,
    minHeight: 0,
  },
  viewerState: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    height: "100%",
    color: MUTED,
    textAlign: "center" as const,
  },
  viewerPre: {
    margin: 0,
    fontFamily: "Consolas, Monaco, monospace",
    fontSize: 13,
    color: TEXT,
    whiteSpace: "pre-wrap" as const,
    wordBreak: "break-word" as const,
  },
  viewerImage: {
    maxWidth: "100%",
    maxHeight: "100%",
    display: "block",
    margin: "0 auto",
  },
  viewerIframe: {
    width: "100%",
    height: "100%",
    border: "none",
    background: "white",
  },
  emptyFiles: {
    height: "100%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: MUTED,
    fontSize: 14,
  },
  fileList: {
    listStyle: "none",
    margin: 0,
    padding: 0,
  },
  fileHeaderRow: {
    display: "grid",
    gridTemplateColumns: "2fr 1fr 1fr",
    padding: "8px 12px",
    fontSize: 12,
    color: MUTED,
    textTransform: "uppercase",
    letterSpacing: 1,
    borderBottom: `1px solid ${BORDER}`,
  },
  fileRow: {
    display: "grid",
    gridTemplateColumns: "2fr 1fr 1fr",
    padding: "10px 12px",
    borderBottom: `1px solid ${BORDER}`,
    fontSize: 14,
    cursor: "pointer",
  },
  colName: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  colSize: { color: MUTED },
  colDate: { color: MUTED },
  draftsSection: {
    marginTop: 20,
    paddingTop: 15,
    borderTop: `1px solid ${BORDER}`,
    display: "flex",
    flexDirection: "column",
    gap: 8,
    overflowY: "auto",
  },
  draftsHeader: {
    display: "flex",
    flexDirection: "column",
    alignItems: "flex-start",
    gap: 4,
  },
  refreshBtn: {
    background: "transparent",
    borderWidth: 1,
    borderStyle: "solid",
    borderColor: BORDER,
    color: TEXT,
    borderRadius: 4,
    cursor: "pointer",
    height: 22,
    fontSize: 10,
    lineHeight: 1,
    padding: 0,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    boxSizing: "border-box",
  },
  iconBtn: {
    width: 25,
  },
  smallTextBtn: {
    width: 52,
  },
  modeBtn: {
    width: 73,
  },
  draftsState: {
    fontSize: 12,
    color: MUTED,
    padding: "4px 0",
  },
  draftsList: {
    listStyle: "none",
    margin: 0,
    padding: 0,
    maxHeight: 260,
    overflowY: "auto",
    display: "flex",
    flexDirection: "column",
    gap: 6,
  },
  draftItem: {
    display: "flex",
    flexDirection: "column",
    gap: 4,
    padding: "8px 10px",
    borderRadius: 6,
    background: PANEL,
    border: `1px solid ${BORDER}`,
    textDecoration: "none",
    color: TEXT,
  },
  draftItemStale: {
    borderColor: "#ffa94d",
    background: "#1f1b13",
  },
  draftConflictButton: {
    cursor: "pointer",
    textAlign: "left",
    width: "100%",
  },
  draftTitle: {
    fontSize: 13,
    fontWeight: 600,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  draftMeta: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 6,
    fontSize: 11,
  },
  draftStatus: {
    padding: "1px 6px",
    borderRadius: 10,
    border: `1px solid ${PURPLE}`,
    color: PURPLE,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  staleStatus: {
    padding: "1px 6px",
    borderRadius: 10,
    border: "1px solid #ffa94d",
    color: "#ffa94d",
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  conflictReviewCta: {
    color: "#ffa94d",
    fontSize: 11,
    fontWeight: 700,
  },
  draftDate: {
    color: MUTED,
  },
  draftItemDelete: {
    cursor: "pointer",
    textAlign: "left",
    width: "100%",
    borderColor: "#ff6b6b",
    color: "#ff6b6b",
  },
  draftItemRename: {
    cursor: "pointer",
    textAlign: "left",
    width: "100%",
    borderColor: PURPLE,
    color: "white",
  },
  draftItemCommit: {
    cursor: "pointer",
    textAlign: "left",
    width: "100%",
    borderColor: "#4fc3f7",
    color: "#4fc3f7",
  },
  rightSectionHeader: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
  },
  headerActions: {
    display: "flex",
    gap: 4,
  },
  rightPanel: {
    display: "flex",
    flexDirection: "column",
    gap: 8,
    overflowY: "auto",
  },
  membersSection: {
    marginTop: 20,
    paddingTop: 15,
    borderTop: `1px solid ${BORDER}`,
    display: "flex",
    flexDirection: "column",
    gap: 8,
  },
  inviteItem: {
    display: "flex",
    flexDirection: "column",
    gap: 4,
    padding: "8px 10px",
    borderRadius: 6,
    background: PANEL,
    border: `1px solid ${BORDER}`,
    color: TEXT,
  },
  inviteActions: {
    display: "flex",
    gap: 6,
    marginTop: 4,
    flexWrap: "wrap",
  },
  inviteActionButton: {
    padding: "5px 9px",
    borderRadius: 4,
    border: `1px solid ${BORDER}`,
    background: "transparent",
    color: TEXT,
    cursor: "pointer",
    fontSize: 11,
    fontWeight: 600,
  },
  inviteAcceptButton: {
    borderColor: PURPLE,
    color: "white",
  },
  inviteDangerButton: {
    borderColor: "#ff6b6b",
    color: "#ff6b6b",
  },
  memberTitleRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8,
  },
  memberRemoveButton: {
    flexShrink: 0,
    padding: "4px 7px",
    borderRadius: 4,
    border: "1px solid #ff6b6b",
    background: "transparent",
    color: "#ff6b6b",
    cursor: "pointer",
    fontSize: 10,
    fontWeight: 700,
  },
  roleButton: {
    padding: "1px 6px",
    borderRadius: 10,
    border: `1px solid ${PURPLE}`,
    background: "transparent",
    color: PURPLE,
    textTransform: "uppercase",
    letterSpacing: 0.5,
    fontSize: 11,
    cursor: "pointer",
  },
  deleteHint: {
    fontSize: 11,
    color: "#ff6b6b",
    padding: "4px 0",
  },
  staleBanner: {
    fontSize: 11,
    color: "#ffa94d",
    border: "1px solid #5c3f12",
    background: "#1f1b13",
    borderRadius: 6,
    padding: "7px 8px",
    lineHeight: 1.4,
  },
  conflictModalContent: {
    maxWidth: 900,
  },
  conflictSummaryRow: {
    display: "flex",
    justifyContent: "space-between",
    gap: 12,
    color: MUTED,
    fontSize: 12,
    marginBottom: 12,
  },
  conflictList: {
    listStyle: "none",
    margin: 0,
    padding: 0,
    display: "flex",
    flexDirection: "column",
    gap: 8,
  },
  conflictItem: {
    border: `1px solid ${BORDER}`,
    borderRadius: 6,
    background: BG,
    padding: "10px 12px",
  },
  conflictFileTop: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
  },
  conflictPath: {
    fontFamily: "Consolas, Monaco, monospace",
    fontSize: 13,
    color: TEXT,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  conflictCategory: {
    flexShrink: 0,
    border: "1px solid",
    borderRadius: 10,
    padding: "1px 7px",
    fontSize: 10,
    textTransform: "uppercase",
    letterSpacing: 0.5,
  },
  conflictHashes: {
    display: "flex",
    flexWrap: "wrap",
    gap: 10,
    marginTop: 7,
    color: MUTED,
    fontFamily: "Consolas, Monaco, monospace",
    fontSize: 11,
  },
  resolutionControls: {
    display: "flex",
    gap: 8,
    marginTop: 10,
  },
  resolutionButton: {
    padding: "5px 9px",
    borderRadius: 5,
    border: `1px solid ${BORDER}`,
    background: "transparent",
    color: TEXT,
    cursor: "pointer",
    fontSize: 11,
    fontWeight: 700,
  },
  resolutionButtonActive: {
    borderColor: "#ffa94d",
    color: "#ffa94d",
    background: "#1f1b13",
  },
  pendingSection: {
    marginTop: 20,
    paddingTop: 12,
    borderTop: `1px solid ${BORDER}`,
  },
  pendingHeader: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    marginBottom: 10,
  },
  commitsState: {
    fontSize: 12,
    color: MUTED,
    padding: "8px 0",
  },
  commitsList: {
    listStyle: "none",
    margin: 0,
    padding: 0,
    maxHeight: 260,
    overflowY: "auto",
  },
  commitItem: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "8px 0",
    borderBottom: `1px solid ${BORDER}`,
    fontSize: 12,
    gap: 8,
  },
  commitContent: {
    display: "flex",
    flexDirection: "column",
    gap: 2,
    flex: 1,
    minWidth: 0,
  },
  commitContentLink: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 2,
    flex: 1,
    minWidth: 0,
    textDecoration: "none",
    color: "inherit",
    cursor: "pointer",
    padding: "4px 6px",
    borderRadius: 4,
    transition: "background-color 0.15s",
    background: "transparent",
    border: "none",
    textAlign: "left",
    width: "100%",
  },
  commitContentSelected: {
    background: "#1f1b13",
    outline: "1px solid #ffa94d",
  },
  commitHashSmall: {
    fontFamily: "monospace",
    color: "#4fc3f7",
  },
  commitStatus: {
    fontSize: 9,
    textTransform: "uppercase" as const,
    letterSpacing: 0.5,
    padding: "1px 6px",
    borderRadius: 10,
    borderWidth: 1,
    borderStyle: "solid" as const,
    fontWeight: 600,
  },
  statusPollButton: {
    background: "transparent",
    border: `1px solid ${BORDER}`,
    color: TEXT,
    borderRadius: 4,
    cursor: "pointer",
    fontSize: 10,
    height: 22,
    padding: "0 7px",
  },
  commitReviewerComment: {
    color: "#ffb86c",
    fontSize: 10,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  commitSummary: {
    color: TEXT,
    fontSize: 12,
    fontWeight: 500,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
  },
  commitMeta: {
    display: "flex",
    gap: 6,
    fontSize: 10,
    color: MUTED,
  },
  commitOwner: {
    fontFamily: "monospace",
  },
  commitDate: {},
  commitActions: {
    position: "relative",
    display: "flex",
    alignItems: "center",
  },
  dotsBtn: {
    background: "transparent",
    border: "none",
    color: TEXT,
    cursor: "pointer",
    fontSize: 16,
    padding: "0 4px",
    lineHeight: 1,
  },
  commitMenu: {
    position: "absolute",
    right: 0,
    top: "100%",
    background: PANEL,
    border: `1px solid ${BORDER}`,
    borderRadius: 6,
    minWidth: 100,
    zIndex: 10,
    boxShadow: "0 4px 10px rgba(0,0,0,0.4)",
    overflow: "hidden",
  },
  menuItem: {
    display: "block",
    width: "100%",
    padding: "8px 12px",
    background: "transparent",
    border: "none",
    color: TEXT,
    textAlign: "left",
    cursor: "pointer",
    fontSize: 12,
  },
  menuItemSuccess: {
    color: "#51cf66",
  },
  menuItemDanger: {
    color: "#ff6b6b",
  },
};
