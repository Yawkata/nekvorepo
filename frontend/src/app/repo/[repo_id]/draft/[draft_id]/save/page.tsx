"use client";

import { CSSProperties, useEffect, useMemo, useState } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";

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

function splitPath(path: string): { dir: string; name: string } {
  const cleaned = path.trim().replace(/^\/+|\/+$/g, "");
  if (!cleaned) return { dir: "", name: "" };
  const parts = cleaned.split("/");
  const name = parts.pop() || "";
  return { dir: parts.join("/"), name };
}

export default function SaveTextFilePage() {
  const router = useRouter();
  const params = useParams();
  const searchParams = useSearchParams();
  const repoId = params?.repo_id as string;
  const draftId = params?.draft_id as string;

  const existingPath = searchParams?.get("path") || "";
  const initialDir = searchParams?.get("dir") || "";
  const pathParts = useMemo(() => splitPath(existingPath), [existingPath]);
  const targetDir = existingPath ? pathParts.dir : initialDir;
  const [fileName, setFileName] = useState(pathParts.name);
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);
  const [loadingFile, setLoadingFile] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setFileName(pathParts.name);
  }, [pathParts.name]);

  useEffect(() => {
    if (!existingPath) {
      setContent("");
      setLoadingFile(false);
      return;
    }

    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }

    let cancelled = false;

    async function loadExistingFile() {
      setLoadingFile(true);
      setError(null);
      try {
        const res = await fetch(
          `/api/repos/${repoId}/drafts/${draftId}/read/${encodeURIComponent(
            existingPath
          )}`,
          {
            headers: { Authorization: `Bearer ${token}` },
          }
        );

        if (res.status === 401) {
          router.push("/login");
          return;
        }

        if (!res.ok) {
          const text = await res.text();
          let data = {};
          try {
            data = text ? JSON.parse(text) : {};
          } catch {
            /* ignore */
          }
          if (!cancelled) {
            setError(extractErrorMessage(data, "Failed to load file"));
          }
          return;
        }

        const nextContent = await res.text();
        if (!cancelled) {
          setContent(nextContent);
        }
      } catch {
        if (!cancelled) {
          setError("Failed to connect to server");
        }
      } finally {
        if (!cancelled) {
          setLoadingFile(false);
        }
      }
    }

    void loadExistingFile();
    return () => {
      cancelled = true;
    };
  }, [draftId, existingPath, repoId, router]);

  async function handleSave() {
    setError(null);
    if (!UUID_RE.test(repoId) || !UUID_RE.test(draftId)) {
      setError("Invalid repo or draft ID.");
      return;
    }

    const cleanedName = fileName.trim().replace(/^\/+|\/+$/g, "");
    if (!cleanedName) {
      setError("Please enter a file name.");
      return;
    }
    if (cleanedName.endsWith(".deleted")) {
      setError("File names ending in .deleted are reserved.");
      return;
    }

    const dir = targetDir.replace(/^\/+|\/+$/g, "");
    const path = (dir ? `${dir}/${cleanedName}` : cleanedName).replace(
      /^\/+/,
      ""
    );

    const token = localStorage.getItem("token");
    if (!token) {
      router.push("/login");
      return;
    }

    setSaving(true);
    try {
      const res = await fetch(
        `/api/repos/${repoId}/drafts/${draftId}/save`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ path, content }),
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
        setError(extractErrorMessage(data, "Failed to save file"));
        return;
      }
      // Success → back to the draft page.
      router.push(`/repo/${repoId}/draft/${draftId}`);
    } catch {
      setError("Failed to connect to server");
    } finally {
      setSaving(false);
    }
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
        <main style={styles.main}>
          <Link
            href={`/repo/${repoId}/draft/${draftId}`}
            style={styles.backLink}
          >
            ← Back to draft
          </Link>

          <h1 style={styles.title}>
            {existingPath ? "Edit text file" : "Save a text file"}
          </h1>

          {targetDir && (
            <p style={styles.dirHint}>
              {existingPath ? "Editing inside " : "Saving inside "}
              <code>{targetDir}/</code>
            </p>
          )}

          {/* Small rectangle: file name */}
          <label style={styles.label}>File name</label>
          <div style={styles.nameBox}>
            <input
              type="text"
              value={fileName}
              onChange={(e) => setFileName(e.target.value)}
              placeholder="e.g. hello.txt or src/hello.txt"
              style={styles.nameInput}
              readOnly={!!existingPath}
            />
          </div>

          {/* Big rectangle: content (60% of screen) */}
          <label style={styles.label}>Content</label>
          <div style={styles.bigRect}>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="Type or paste the file contents here…"
              style={styles.textarea}
              spellCheck={false}
              readOnly={loadingFile}
            />

            <div style={styles.rectFooter}>
              <button
                onClick={handleSave}
                disabled={saving || loadingFile}
                style={{
                  ...styles.saveBtn,
                  ...((saving || loadingFile)
                    ? { opacity: 0.6, cursor: "wait" }
                    : {}),
                }}
              >
                {loadingFile ? "Loading…" : saving ? "Saving…" : "Save"}
              </button>
              {error && <div style={styles.errorText}>{error}</div>}
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
  avatar: { width: 30, height: 30, borderRadius: "50%", background: "#ccc" },
  content: { display: "flex", flex: 1 },
  main: {
    flex: 1,
    padding: "30px 40px",
    display: "flex",
    flexDirection: "column",
    alignItems: "flex-start",
  },
  backLink: {
    color: MUTED,
    textDecoration: "none",
    fontSize: 13,
    marginBottom: 10,
  },
  title: { margin: "0 0 8px", fontSize: 26, color: "white" },
  dirHint: { margin: "0 0 18px", color: MUTED, fontSize: 13 },
  label: {
    fontSize: 12,
    color: MUTED,
    textTransform: "uppercase",
    letterSpacing: 1,
    marginTop: 10,
    marginBottom: 6,
  },

  /* Small rectangle for the file name */
  nameBox: {
    background: PANEL,
    border: `1px solid ${PURPLE}`,
    borderRadius: 8,
    padding: 8,
    width: "min(420px, 60vw)",
    marginBottom: 14,
  },
  nameInput: {
    width: "100%",
    background: "transparent",
    border: "none",
    outline: "none",
    color: TEXT,
    fontSize: 14,
    padding: "4px 6px",
  },

  /* Big rectangle = 60% of viewport on both axes */
  bigRect: {
    width: "60vw",
    height: "60vh",
    background: PANEL,
    borderWidth: 1,
    borderStyle: "solid",
    borderColor: PURPLE,
    borderRadius: 10,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  },
  textarea: {
    flex: 1,
    width: "100%",
    background: "transparent",
    color: TEXT,
    border: "none",
    outline: "none",
    padding: 14,
    resize: "none",
    fontFamily: "Consolas, 'Courier New', monospace",
    fontSize: 13,
    lineHeight: 1.5,
  },
  rectFooter: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    padding: "10px 14px",
    borderTopWidth: 1,
    borderTopStyle: "solid",
    borderTopColor: BORDER,
  },
  saveBtn: {
    background: PURPLE,
    color: "white",
    borderWidth: 1,
    borderStyle: "solid",
    borderColor: PURPLE,
    borderRadius: 6,
    padding: "8px 18px",
    cursor: "pointer",
    fontWeight: 600,
    fontSize: 13,
  },
  errorText: { color: "#ff6b6b", fontSize: 13 },
};
