"use client";

import { CSSProperties, useEffect, useMemo, useState } from "react";
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
  updated_at?: string;
  created_at?: string;
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
  created_at?: string;
  updated_at?: string;
  visibility?: string;
  role?: Role | string;
  files?: RepoFile[];
};

type ApiErrorData = {
  detail?: unknown;
  error?: unknown;
};

type VerifyMeResponse = {
  data?: {
    email?: string;
  };
};

type ToolbarAction = {
  label: string;
  variant?: "primary" | "secondary" | "danger";
  onClick?: () => void;
};

function actionsForRole(
  role: string | undefined,
  handlers: { onNewDraft: () => void; onInviteMember: () => void; inviting: boolean }
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
        { label: "Review Queue", variant: "secondary" },
        inviteMember,
        { label: "Manage", variant: "secondary" },
        { label: "View Current", variant: "secondary" },
        { label: "Download ZIP", variant: "secondary" },
        { label: "Delete Repository", variant: "danger" },
      ];
    case "author":
      return [
        newDraft,
        { label: "View Current", variant: "secondary" },
        { label: "Download ZIP", variant: "secondary" },
      ];
    case "reviewer":
      return [
        { label: "Review Queue", variant: "primary" },
        { label: "Download ZIP", variant: "secondary" },
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

function isErrorItem(value: unknown): value is { msg?: unknown; loc?: unknown } {
  return typeof value === "object" && value !== null;
}

function extractErrorMessage(data: unknown, fallback: string): string {
  if (!data || typeof data !== "object") return fallback;
  const source = data as ApiErrorData;
  const d = source.detail ?? source.error;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    return d
      .map((item) => {
        if (typeof item === "string") return item;
        if (isErrorItem(item) && typeof item.msg === "string") {
          const prefix = Array.isArray(item.loc) ? `${item.loc.join(".")}: ` : "";
          return `${prefix}${item.msg}`;
        }
        return JSON.stringify(item);
      })
      .join("; ");
  }
  if (isErrorItem(d) && typeof d.msg === "string") return d.msg;
  if (d && typeof d === "object") return JSON.stringify(d);
  return fallback;
}

function formatSize(bytes?: number): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function RepoPage() {
  const router = useRouter();
  const params = useParams();
  const repoId = params?.repo_id as string;

  const [repo, setRepo] = useState<Repo | null>(null);
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
  const [inviting, setInviting] = useState(false);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [invitesLoading, setInvitesLoading] = useState(false);
  const [invitesError, setInvitesError] = useState<string | null>(null);
  const [invitesOpen, setInvitesOpen] = useState(true);
  const [acceptingInviteId, setAcceptingInviteId] = useState<string | null>(null);
  const [resendingInviteId, setResendingInviteId] = useState<string | null>(null);
  const [revokingInviteId, setRevokingInviteId] = useState<string | null>(null);
  const [currentUserEmail, setCurrentUserEmail] = useState<string | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [membersError, setMembersError] = useState<string | null>(null);
  const [membersOpen, setMembersOpen] = useState(true);
  const [changingRoleUserId, setChangingRoleUserId] = useState<string | null>(null);
  const [removingMemberId, setRemovingMemberId] = useState<string | null>(null);

  const isAdmin = String(repo?.role || "").toLowerCase() === "admin";
  const normalizedCurrentUserEmail = currentUserEmail?.trim().toLowerCase();

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
      let data: ApiErrorData & { invited_email?: string } = {};
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

      alert(`Invite sent to ${data.invited_email || email}.`);
      loadInvites();
    } catch {
      alert("Failed to connect to server");
    } finally {
      setInviting(false);
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
      const res = await fetch(
        `/api/repos/${repoId}/invites/${invite.token_id}/resend`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        }
      );

      const text = await res.text();
      let data: ApiErrorData = {};
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

      loadInvites();
    } catch {
      alert("Failed to connect to server");
    } finally {
      setResendingInviteId(null);
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
      const res = await fetch(
        `/api/repos/${repoId}/invites/${invite.token_id}/accept`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        }
      );

      const text = await res.text();
      let data: ApiErrorData = {};
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

      setInvites((prev) =>
        prev.filter((item) => item.token_id !== invite.token_id)
      );
    } catch {
      alert("Failed to connect to server");
    } finally {
      setAcceptingInviteId(null);
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
      const res = await fetch(
        `/api/repos/${repoId}/invites/${invite.token_id}/revoke`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
        }
      );

      const text = await res.text();
      let data: ApiErrorData = {};
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

      setInvites((prev) =>
        prev.filter((item) => item.token_id !== invite.token_id)
      );
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
      let data: ApiErrorData & { role?: string } = {};
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
          item.user_id === member.user_id
            ? { ...item, role: data.role || roleInput }
            : item
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
      let data: ApiErrorData = {};
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

      setMembers((prev) =>
        prev.filter((item) => item.user_id !== member.user_id)
      );
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
      let data: ApiErrorData = {};
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
        let data: ApiErrorData = {};
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
      const data: ApiErrorData &
        Partial<Draft> & { draft_id?: string; id?: string } = text
        ? JSON.parse(text)
        : {};

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId, router]);

  useEffect(() => {
    if (currentUserEmail) return;
    const token = localStorage.getItem("token");
    if (!token) return;

    fetch("/api/auth/verify-me", {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(async (res) => {
        if (!res.ok) return null;
        return (await res.json()) as VerifyMeResponse;
      })
      .then((data) => {
        if (data?.data?.email) {
          setCurrentUserEmail(data.data.email);
          localStorage.setItem("email", data.data.email);
        }
      })
      .catch(() => {
        /* Non-fatal: the accept button simply remains hidden without email. */
      });
  }, [currentUserEmail]);

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

  const files: RepoFile[] = useMemo(() => repo?.files ?? [], [repo?.files]);

  const filteredFiles = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return files;
    return files.filter((f) => f.name.toLowerCase().includes(q));
  }, [files, search]);

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

          <div style={styles.draftsSection}>
            <div style={styles.draftsHeader}>
              <label style={styles.sidebarLabel}>Drafts</label>
              <div style={{ display: "flex", gap: 4 }}>
                <button
                  onClick={loadDrafts}
                  style={{ ...styles.refreshBtn, ...styles.iconBtn }}
                  title="Refresh drafts"
                >
                  ↻
                </button>
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
              </div>
            </div>
            {renameMode && drafts.length > 0 && (
              <div style={styles.deleteHint}>
                Click a draft to rename it.
              </div>
            )}
            {deleteMode && drafts.length > 0 && (
              <div style={styles.deleteHint}>
                Click a draft to delete it.
              </div>
            )}

            {draftsLoading ? (
              <div style={styles.draftsState}>Loading…</div>
            ) : draftsError ? (
              <div style={{ ...styles.draftsState, color: "#ff6b6b" }}>
                {draftsError}
              </div>
            ) : drafts.length === 0 ? (
              <div style={styles.draftsState}>No drafts yet.</div>
            ) : (
              <ul style={styles.draftsList}>
                {drafts.map((d) => {
                  const id = d.draft_id ?? d.id ?? "";
                  if (!id) return null;
                  const title =
                    (d.label && d.label.trim()) ||
                    `Draft — ${formatDate(d.created_at)}`;
                  const isDeleting = deletingId === id;
                  const isRenaming = renamingId === id;

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
                      ) : (
                        <Link
                          href={`/repo/${repoId}/draft/${id}`}
                          style={styles.draftItem}
                        >
                          {content}
                        </Link>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
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
                    <dd style={styles.metaVal}>
                      {repo.owner_email || repo.owner_id || "—"}
                    </dd>
                  </div>
                  <div style={styles.metaItem}>
                    <dt style={styles.metaKey}>Created</dt>
                    <dd style={styles.metaVal}>{formatDate(repo.created_at)}</dd>
                  </div>
                  <div style={styles.metaItem}>
                    <dt style={styles.metaKey}>Last updated</dt>
                    <dd style={styles.metaVal}>{formatDate(repo.updated_at)}</dd>
                  </div>
                  <div style={styles.metaItem}>
                    <dt style={styles.metaKey}>Created At</dt>
                    <dd style={styles.metaVal}>{formatDateTime(repo.created_at)}</dd>
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
                  }).map((action) => {
                    const isNewDraft = action.label === "+ New Draft";
                    const disabled = isNewDraft && creatingDraft;
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
                  {filteredFiles.length === 0 ? (
                    <div style={styles.emptyFiles}>
                      {files.length === 0
                        ? "No files in this repository yet."
                        : "No files match your search."}
                    </div>
                  ) : (
                    <ul style={styles.fileList}>
                      <li style={styles.fileHeaderRow}>
                        <span style={styles.colName}>Name</span>
                        <span style={styles.colSize}>Size</span>
                        <span style={styles.colDate}>Updated</span>
                      </li>
                      {filteredFiles.map((f) => (
                        <li key={f.name} style={styles.fileRow}>
                          <span style={styles.colName}>{f.name}</span>
                          <span style={styles.colSize}>{formatSize(f.size)}</span>
                          <span style={styles.colDate}>{formatDate(f.updated_at)}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            </>
          ) : null}
        </main>

        {isAdmin && (
          <aside style={{ ...styles.sidebar, ...styles.rightSidebar }}>
            <div style={styles.invitesHeader}>
              <label style={styles.sidebarLabel}>Invites</label>
              <div style={{ display: "flex", gap: 4 }}>
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
                  aria-expanded={invitesOpen}
                >
                  {invitesOpen ? "Hide" : "Show"}
                </button>
              </div>
            </div>

            {invitesOpen && (
              <div style={styles.invitesPanel}>
                {invitesLoading ? (
                  <div style={styles.draftsState}>LoadingвЂ¦</div>
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
                        <span style={styles.draftTitle}>
                          {invite.invited_email}
                        </span>
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
                              revokingInviteId === invite.token_id ||
                              resendingInviteId === invite.token_id
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
              <div style={styles.invitesHeader}>
                <label style={styles.sidebarLabel}>Members</label>
                <div style={{ display: "flex", gap: 4 }}>
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
                    aria-expanded={membersOpen}
                  >
                    {membersOpen ? "Hide" : "Show"}
                  </button>
                </div>
              </div>

              {membersOpen && (
                <div style={styles.invitesPanel}>
                  {membersLoading ? (
                    <div style={styles.draftsState}>LoadingРІР‚В¦</div>
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
    width: 260,
    borderRight: `1px solid ${PURPLE}`,
    padding: 20,
    display: "flex",
    flexDirection: "column",
    gap: 10,
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
    alignItems: "center",
    justifyContent: "space-between",
  },
  invitesHeader: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
  },
  invitesPanel: {
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
  inviteActions: {
    display: "flex",
    gap: 6,
    marginTop: 4,
    flexWrap: "wrap",
  },
  inviteAcceptButton: {
    borderColor: PURPLE,
    color: "white",
  },
  inviteDangerButton: {
    borderColor: "#ff6b6b",
    color: "#ff6b6b",
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
  deleteHint: {
    fontSize: 11,
    color: "#ff6b6b",
    padding: "4px 0",
  },
};
