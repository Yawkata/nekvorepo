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
  updated_at?: string;
  created_at?: string;
};

type Commit = {
  commit_hash: string;
  status: "pending" | "approved" | "rejected" | "sibling_rejected";
  commit_summary: string;
  commit_description?: string;
  changes_summary?: string;
  owner_id: string;
  timestamp?: string;
  draft_id?: string;
  reviewer_comment?: string;
  parent_commit_hash?: string;
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
  handlers: { onNewDraft: () => void }
): ToolbarAction[] {
  const normalized = (role || "").toLowerCase();
  const newDraft: ToolbarAction = {
    label: "+ New Draft",
    variant: "primary",
    onClick: handlers.onNewDraft,
  };
  switch (normalized) {
    case "admin":
      return [
        newDraft,
        { label: "Review Queue", variant: "secondary" },
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

  async function loadView() {
    const token = localStorage.getItem("token");
    if (!token || !repoId) return;

    setViewLoading(true);
    setViewError(null);
    try {
      const res = await fetch(`/api/repos/${repoId}/view`, {
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
    } catch {
      setViewError("Failed to connect to server");
    } finally {
      setViewLoading(false);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId, router]);

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
            {commitMode && drafts.length > 0 && (
              <div style={styles.deleteHint}>
                Click a draft to submit it for review.
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
                      ) : commitMode ? (
                        <button
                          onClick={() => handleDraftForCommit(d)}
                          disabled={submittingCommit}
                          style={{
                            ...styles.draftItem,
                            ...styles.draftItemCommit,
                            ...(submittingCommit
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
                              <span style={styles.commitOwner}>{commit.owner_id.substring(0, 8)}</span>
                              <span style={styles.commitDate}>{formatDateTime(commit.timestamp)}</span>
                            </span>
                          </div>
                        )}
                        <div style={styles.commitActions}>
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
                      {commit.draft_id ? (
                        <Link
                          href={`/repo/${repoId}/draft/${commit.draft_id}`}
                          style={styles.commitContentLink}
                          title={title}
                        >
                          {content}
                        </Link>
                      ) : (
                        <div style={styles.commitContent} title={title}>
                          {content}
                        </div>
                      )}
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
                    <dd style={styles.metaVal}>{formatDate(repo.updated_at)}</dd>
                  </div>
                </dl>
              </section>

              {/* FILES SECTION */}
              <div style={styles.filesWrapper}>
                <div style={styles.filesToolbar}>
                  {actionsForRole(repo.role, {
                    onNewDraft: handleNewDraft,
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
                  <div style={styles.viewBanner}>
                    <span style={styles.viewBannerLabel}>
                      {viewCommitHash ? "Latest accepted commit" : "Committed files"}
                    </span>
                    {viewCommitHash && (
                      <span style={styles.viewBannerHash}>
                        #{viewCommitHash.substring(0, 10)}
                      </span>
                    )}
                    <button
                      onClick={loadView}
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
  draftItemCommit: {
    cursor: "pointer",
    textAlign: "left",
    width: "100%",
    borderColor: "#4fc3f7",
    color: "#4fc3f7",
  },
  deleteHint: {
    fontSize: 11,
    color: "#ff6b6b",
    padding: "4px 0",
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
