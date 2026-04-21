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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId, router]);

  const files: RepoFile[] = repo?.files ?? [];

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
  deleteHint: {
    fontSize: 11,
    color: "#ff6b6b",
    padding: "4px 0",
  },
};
