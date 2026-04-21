"use client";

import {
  CSSProperties,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";

type ExplorerEntry = {
  name: string;
  path?: string;
  type?: "file" | "folder" | "blob" | "tree";
  size?: number;
  is_binary?: boolean;
  updated_at?: string;
};

type Draft = {
  draft_id?: string;
  id?: string;
  label?: string | null;
  status?: string;
  base_commit_hash?: string | null;
  updated_at?: string;
  created_at?: string;
};

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

function formatSize(bytes?: number): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function DraftPage() {
  const router = useRouter();
  const params = useParams();
  const repoId = params?.repo_id as string;
  const draftId = params?.draft_id as string;

  const [draft, setDraft] = useState<Draft | null>(null);
  const [entries, setEntries] = useState<ExplorerEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [currentPath, setCurrentPath] = useState<string>("");

  const [uploading, setUploading] = useState(false);
  const [creatingFolder, setCreatingFolder] = useState(false);
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [renamingKey, setRenamingKey] = useState<string | null>(null);
  const [localFolders, setLocalFolders] = useState<ExplorerEntry[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }
    if (!repoId || !draftId) return;
    if (!UUID_RE.test(repoId) || !UUID_RE.test(draftId)) {
      setError("Invalid repo or draft ID.");
      setLoading(false);
      return;
    }

    // Pull the draft metadata from the repo's drafts list.
    fetch(`/api/repos/${repoId}/drafts`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(async (res) => {
        if (res.status === 401) {
          router.push("/login");
          return null;
        }
        const data = await res.json().catch(() => []);
        if (!res.ok) return null;
        return Array.isArray(data) ? data : [];
      })
      .then((list) => {
        if (!list) return;
        const found = list.find(
          (d: Draft) => (d.draft_id ?? d.id) === draftId
        );
        if (found) setDraft(found);
      })
      .catch(() => {});

    loadExplorer();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repoId, draftId, router]);

  async function loadExplorer() {
    const token = localStorage.getItem("token");
    if (!token || !repoId || !draftId) return;

    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/repos/${repoId}/drafts/${draftId}/explorer`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      const text = await res.text();
      const data = text ? JSON.parse(text) : [];
      if (!res.ok) {
        setError(extractErrorMessage(data, "Failed to load draft files"));
        return;
      }
      const list: ExplorerEntry[] = Array.isArray(data)
        ? data
        : Array.isArray(data.entries)
        ? data.entries
        : [];
      setEntries(list);
    } catch {
      setError("Failed to connect to server");
    } finally {
      setLoading(false);
    }
  }

  function handleSaveText() {
    // Dedicated editor page — carries the current directory forward so the
    // new file lands where the user was browsing.
    const qs = currentPath
      ? `?dir=${encodeURIComponent(currentPath)}`
      : "";
    router.push(`/repo/${repoId}/draft/${draftId}/save${qs}`);
  }

  function triggerUpload() {
    fileInputRef.current?.click();
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow selecting the same file again later
    if (!file) return;

    if (file.size > 100 * 1024 * 1024) {
      alert("Maximum file size is 100 MB.");
      return;
    }
    if (file.name.endsWith(".deleted")) {
      alert("File names ending in .deleted are reserved.");
      return;
    }

    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }

    // Target path = current directory + chosen file name (no leading slash).
    const uploadPath = (currentPath ? `${currentPath}/${file.name}` : file.name)
      .replace(/^\/+/, "");

    setUploading(true);
    try {
      const form = new FormData();
      form.append("path", uploadPath);
      form.append("file", file, file.name);

      const res = await fetch(
        `/api/repos/${repoId}/drafts/${draftId}/upload`,
        {
          method: "POST",
          headers: { Authorization: `Bearer ${token}` },
          body: form,
        }
      );
      const text = await res.text();
      const data = text ? JSON.parse(text) : {};
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      if (!res.ok) {
        alert(extractErrorMessage(data, "Failed to upload file"));
        return;
      }
      await loadExplorer();
    } catch {
      alert("Failed to connect to server");
    } finally {
      setUploading(false);
    }
  }

  async function handleCreateFolder() {
    if (creatingFolder) return;
    const hint = currentPath
      ? `New folder inside "${currentPath}":`
      : "New folder path (e.g. src/utils):";
    const raw = window.prompt(hint);
    if (!raw) return;
    const cleaned = raw.trim().replace(/^\/+|\/+$/g, "");
    if (!cleaned) return;
    const trimmed = currentPath ? `${currentPath}/${cleaned}` : cleaned;

    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }

    setCreatingFolder(true);
    try {
      const res = await fetch(
        `/api/repos/${repoId}/drafts/${draftId}/mkdir`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ path: trimmed }),
        }
      );
      const text = await res.text();
      const data = text ? JSON.parse(text) : {};
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      if (!res.ok) {
        alert(extractErrorMessage(data, "Failed to create folder"));
        return;
      }
      // Optimistically show the folder — empty folders may not be
      // returned by the explorer endpoint until a file is added inside.
      setLocalFolders((prev) => {
        if (prev.some((f) => (f.path || f.name) === trimmed)) return prev;
        const name = trimmed.split("/").filter(Boolean).pop() || trimmed;
        return [
          ...prev,
          { name, path: trimmed, type: "folder" },
        ];
      });
      await loadExplorer();
    } catch {
      alert("Failed to connect to server");
    } finally {
      setCreatingFolder(false);
    }
  }

  async function handleRename(entry: ExplorerEntry) {
    const fromPath = entry.path || entry.name;
    const nextPath = window.prompt(`Rename "${fromPath}" to:`, fromPath);
    if (!nextPath) return;
    const trimmed = nextPath.trim();
    if (!trimmed || trimmed === fromPath) return;
    if (trimmed.endsWith(".deleted")) {
      alert("File names ending in .deleted are reserved.");
      return;
    }

    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }

    const key = entry.path || entry.name;
    setRenamingKey(key);
    setOpenMenu(null);
    try {
      const res = await fetch(
        `/api/repos/${repoId}/drafts/${draftId}/rename`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ from_path: fromPath, to_path: trimmed }),
        }
      );
      const text = await res.text();
      const data = text ? JSON.parse(text) : {};
      if (res.status === 401) {
        router.push("/login");
        return;
      }
      if (!res.ok) {
        alert(extractErrorMessage(data, "Failed to rename"));
        return;
      }
      // If we renamed a locally-tracked folder, update its bookkeeping so
      // the rectangle doesn't keep showing the old name.
      setLocalFolders((prev) =>
        prev.map((f) => {
          const p = f.path || f.name;
          if (p === fromPath) {
            const name = trimmed.split("/").filter(Boolean).pop() || trimmed;
            return { ...f, name, path: trimmed };
          }
          return f;
        })
      );
      await loadExplorer();
    } catch {
      alert("Failed to connect to server");
    } finally {
      setRenamingKey(null);
    }
  }

  const mergedEntries = useMemo(() => {
    if (localFolders.length === 0) return entries;
    const seen = new Set(entries.map((e) => e.path || e.name));
    const extras = localFolders.filter(
      (f) => !seen.has(f.path || f.name)
    );
    return [...extras, ...entries];
  }, [entries, localFolders]);

  // Flatten the merged entries into "what lives at currentPath".
  // Entries whose path lies deeper than currentPath are represented by
  // their immediate-child folder at this level, so the user can drill down
  // even if the backend doesn't return empty-folder rows explicitly.
  const entriesAtCurrentPath = useMemo(() => {
    const prefix = currentPath ? currentPath + "/" : "";
    const folderNames = new Set<string>();
    const files: ExplorerEntry[] = [];
    const folderEntries: ExplorerEntry[] = [];

    for (const e of mergedEntries) {
      const fullPath = e.path || e.name;
      if (currentPath) {
        if (!fullPath.startsWith(prefix)) continue;
      }
      const relative = currentPath ? fullPath.slice(prefix.length) : fullPath;
      if (!relative) continue;
      const slash = relative.indexOf("/");
      const isFolderType = e.type === "folder" || e.type === "tree";

      if (slash === -1) {
        // Direct child of currentPath.
        if (isFolderType) {
          if (!folderNames.has(relative)) {
            folderNames.add(relative);
            folderEntries.push({ ...e, name: relative, path: fullPath });
          }
        } else {
          files.push({ ...e, name: relative, path: fullPath });
        }
      } else {
        // Deeper descendant — surface its top-level folder.
        const folderName = relative.slice(0, slash);
        if (!folderNames.has(folderName)) {
          folderNames.add(folderName);
          folderEntries.push({
            name: folderName,
            path: prefix + folderName,
            type: "folder",
          });
        }
      }
    }

    // Folders first, then files, each alphabetised.
    folderEntries.sort((a, b) => a.name.localeCompare(b.name));
    files.sort((a, b) => a.name.localeCompare(b.name));
    return [...folderEntries, ...files];
  }, [mergedEntries, currentPath]);

  const filteredEntries = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return entriesAtCurrentPath;
    return entriesAtCurrentPath.filter((e) =>
      e.name.toLowerCase().includes(q)
    );
  }, [entriesAtCurrentPath, search]);

  const breadcrumbs = useMemo(() => {
    if (!currentPath) return [];
    const parts = currentPath.split("/").filter(Boolean);
    const acc: { label: string; path: string }[] = [];
    parts.forEach((part, idx) => {
      acc.push({ label: part, path: parts.slice(0, idx + 1).join("/") });
    });
    return acc;
  }, [currentPath]);

  function openFolder(path: string) {
    setCurrentPath(path);
    setSearch("");
    setOpenMenu(null);
  }

  const draftTitle =
    (draft?.label && draft.label.trim()) ||
    `Draft — ${formatDate(draft?.created_at)}`;

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
        {/* LEFT SIDEBAR */}
        <aside style={styles.sidebar}>
          <Link href={`/repo/${repoId}`} style={styles.backLink}>
            ← Back to repository
          </Link>

          <label style={styles.sidebarLabel}>Search files</label>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Type a file name…"
            style={styles.searchInput}
          />
          <div style={styles.searchMeta}>
            {filteredEntries.length} / {mergedEntries.length} items
          </div>
        </aside>

        {/* MAIN */}
        <main style={styles.main}>
          {/* HEADER */}
          <section style={styles.metaHeader}>
            <div style={styles.metaTitleRow}>
              <h1 style={styles.repoTitle}>{draftTitle}</h1>
              {draft?.status && (
                <span style={styles.badge}>{draft.status}</span>
              )}
            </div>
            <p style={styles.description}>
              Files in this draft are stored in EFS until you commit. Any file
              names ending in <code>.deleted</code> are reserved.
            </p>

          </section>

          {/* FILES SECTION */}
          <div style={styles.filesWrapper}>
            <div style={styles.filesToolbar}>
              <button
                onClick={handleSaveText}
                style={{
                  ...styles.toolbarButton,
                  ...styles.btnSecondary,
                }}
              >
                Save a text file
              </button>

              <button
                onClick={triggerUpload}
                disabled={uploading}
                style={{
                  ...styles.toolbarButton,
                  ...styles.btnSecondary,
                  ...(uploading ? { opacity: 0.6, cursor: "wait" } : {}),
                }}
              >
                {uploading ? "Uploading…" : "Upload binary file"}
              </button>

              <button
                onClick={handleCreateFolder}
                disabled={creatingFolder}
                style={{
                  ...styles.toolbarButton,
                  ...styles.btnSecondary,
                  ...(creatingFolder ? { opacity: 0.6, cursor: "wait" } : {}),
                }}
              >
                {creatingFolder ? "Creating…" : "Create folder"}
              </button>

              <input
                ref={fileInputRef}
                type="file"
                onChange={handleUpload}
                style={{ display: "none" }}
              />
            </div>

            <div style={styles.breadcrumbs}>
              <button
                onClick={() => openFolder("")}
                style={{
                  ...styles.crumb,
                  ...(currentPath ? {} : styles.crumbActive),
                }}
                disabled={!currentPath}
              >
                root
              </button>
              {breadcrumbs.map((b) => (
                <span
                  key={b.path}
                  style={{ display: "inline-flex", alignItems: "center" }}
                >
                  <span style={styles.crumbSep}>/</span>
                  <button
                    onClick={() => openFolder(b.path)}
                    style={{
                      ...styles.crumb,
                      ...(b.path === currentPath ? styles.crumbActive : {}),
                    }}
                    disabled={b.path === currentPath}
                  >
                    {b.label}
                  </button>
                </span>
              ))}
            </div>

            <div style={styles.filesRect}>
              {loading ? (
                <div style={styles.emptyFiles}>Loading draft files…</div>
              ) : error ? (
                <div style={{ ...styles.emptyFiles, color: "#ff6b6b" }}>
                  {error}
                </div>
              ) : filteredEntries.length === 0 ? (
                <div style={styles.emptyFiles}>
                  {mergedEntries.length === 0
                    ? "This draft has no files yet. Use the buttons above to add some."
                    : "No files match your search."}
                </div>
              ) : (
                <ul style={styles.fileList}>
                  <li style={styles.fileHeaderRow}>
                    <span style={styles.colName}>Name</span>
                    <span style={styles.colSize}>Size</span>
                    <span style={styles.colDate}>Type</span>
                    <span style={styles.colActions}></span>
                  </li>
                  {filteredEntries.map((f) => {
                    const isFolder =
                      f.type === "folder" || f.type === "tree";
                    const kind = isFolder
                      ? "folder"
                      : f.is_binary
                      ? "binary"
                      : "text";
                    const key = f.path || f.name;
                    const isRenaming = renamingKey === key;
                    const menuOpen = openMenu === key;
                    return (
                      <li key={key} style={styles.fileRow}>
                        <span style={styles.colName}>
                          {isFolder ? (
                            <button
                              onClick={() => openFolder(f.path || f.name)}
                              style={styles.folderLink}
                              title="Open folder"
                            >
                              📁 {f.name}
                            </button>
                          ) : (
                            <>
                              📄 {f.name}
                            </>
                          )}
                          {isRenaming && (
                            <span style={styles.inlineNote}> (renaming…)</span>
                          )}
                        </span>
                        <span style={styles.colSize}>
                          {isFolder ? "—" : formatSize(f.size)}
                        </span>
                        <span style={styles.colDate}>{kind}</span>
                        <span style={styles.colActions}>
                          <button
                            onClick={() =>
                              setOpenMenu((cur) => (cur === key ? null : key))
                            }
                            style={styles.dotsBtn}
                            aria-label="More actions"
                            title="More actions"
                          >
                            ⋮
                          </button>
                          {menuOpen && (
                            <div style={styles.menu}>
                              <button
                                onClick={() => handleRename(f)}
                                style={styles.menuItem}
                              >
                                Rename
                              </button>
                            </div>
                          )}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            <div style={styles.createdLine}>
              Created on {formatDateTime(draft?.created_at)}
            </div>
          </div>
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
  brand: { color: "white", textDecoration: "none" },
  avatar: {
    width: 30,
    height: 30,
    borderRadius: "50%",
    background: "#ccc",
  },
  content: { display: "flex", flex: 1 },
  sidebar: {
    width: 260,
    borderRight: `1px solid ${PURPLE}`,
    padding: 20,
    display: "flex",
    flexDirection: "column",
    gap: 10,
  },
  backLink: {
    color: MUTED,
    textDecoration: "none",
    fontSize: 12,
    marginBottom: 10,
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
  searchMeta: { fontSize: 12, color: MUTED },
  main: {
    flex: 1,
    padding: "30px 40px",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
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
  repoTitle: { margin: 0, fontSize: 26, color: "white" },
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
  metaVal: { margin: 0, color: TEXT, fontSize: 14 },
  filesWrapper: { width: "45vw", display: "flex", flexDirection: "column" },
  filesToolbar: {
    display: "flex",
    flexWrap: "wrap",
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
    textAlign: "center",
    padding: 20,
  },
  fileList: { listStyle: "none", margin: 0, padding: 0 },
  fileHeaderRow: {
    display: "grid",
    gridTemplateColumns: "2fr 1fr 1fr 40px",
    alignItems: "center",
    padding: "8px 12px",
    fontSize: 12,
    color: MUTED,
    textTransform: "uppercase",
    letterSpacing: 1,
    borderBottom: `1px solid ${BORDER}`,
  },
  fileRow: {
    display: "grid",
    gridTemplateColumns: "2fr 1fr 1fr 40px",
    alignItems: "center",
    padding: "10px 12px",
    borderBottom: `1px solid ${BORDER}`,
    fontSize: 14,
  },
  colName: {
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  colSize: { color: MUTED },
  colDate: { color: MUTED },
  colActions: {
    position: "relative",
    display: "flex",
    justifyContent: "flex-end",
  },
  dotsBtn: {
    background: "transparent",
    border: "none",
    color: TEXT,
    cursor: "pointer",
    fontSize: 18,
    padding: "0 6px",
    lineHeight: 1,
  },
  menu: {
    position: "absolute",
    right: 0,
    top: "100%",
    background: PANEL,
    border: `1px solid ${BORDER}`,
    borderRadius: 6,
    minWidth: 120,
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
    fontSize: 13,
  },
  inlineNote: { color: MUTED, fontSize: 11, marginLeft: 6 },
  createdLine: {
    marginTop: 10,
    textAlign: "left",
    color: MUTED,
    fontSize: 12,
  },
  breadcrumbs: {
    display: "flex",
    flexWrap: "wrap",
    alignItems: "center",
    gap: 2,
    marginBottom: 6,
    fontSize: 13,
    color: MUTED,
  },
  crumb: {
    background: "transparent",
    border: "none",
    color: MUTED,
    cursor: "pointer",
    padding: "2px 6px",
    borderRadius: 4,
    fontSize: 13,
  },
  crumbActive: {
    color: TEXT,
    cursor: "default",
    fontWeight: 600,
  },
  crumbSep: {
    color: BORDER,
    padding: "0 2px",
  },
  folderLink: {
    background: "transparent",
    border: "none",
    color: TEXT,
    cursor: "pointer",
    padding: 0,
    font: "inherit",
    textAlign: "left",
  },
};
