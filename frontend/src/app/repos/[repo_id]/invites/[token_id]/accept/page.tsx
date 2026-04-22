"use client";

import { CSSProperties, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

type Repo = {
  repo_name?: string;
  owner_email?: string;
  owner_id?: string;
};

type ApiErrorData = {
  detail?: unknown;
  error?: unknown;
};

function extractErrorMessage(data: unknown, fallback: string): string {
  if (!data || typeof data !== "object") return fallback;
  const source = data as ApiErrorData;
  const detail = source.detail ?? source.error;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") return JSON.stringify(detail);
  return fallback;
}

export default function InviteAcceptPage() {
  const params = useParams();
  const router = useRouter();
  const repoId = params?.repo_id as string;
  const tokenId = params?.token_id as string;

  const [repo, setRepo] = useState<Repo | null>(null);
  const [loadingRepo, setLoadingRepo] = useState(true);
  const [accepting, setAccepting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [accepted, setAccepted] = useState(false);

  const repoName = repo?.repo_name || "this repository";
  const owner = repo?.owner_email || repo?.owner_id || "The repository owner";

  useEffect(() => {
    const token = localStorage.getItem("token");
    if (!token || !repoId) {
      setLoadingRepo(false);
      return;
    }

    fetch(`/api/repos/${repoId}`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(async (res) => {
        if (!res.ok) return null;
        return (await res.json()) as Repo;
      })
      .then((data) => {
        if (data) setRepo(data);
      })
      .catch(() => {
        /* Repo details are optional before the invite is accepted. */
      })
      .finally(() => setLoadingRepo(false));
  }, [repoId]);

  async function handleAccept() {
    if (accepting) return;
    const token = localStorage.getItem("token");
    if (!token) {
      setMessage("Please log in with the invited email address, then return to this invitation link.");
      return;
    }

    setAccepting(true);
    setMessage(null);
    try {
      const res = await fetch(`/api/repos/${repoId}/invites/${tokenId}/accept`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });

      const text = await res.text();
      let data: ApiErrorData = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        /* ignore */
      }

      if (!res.ok) {
        setMessage(extractErrorMessage(data, "Failed to accept invitation"));
        return;
      }

      setAccepted(true);
      setMessage("Invitation accepted.");
      setTimeout(() => router.push(`/repo/${repoId}`), 900);
    } catch {
      setMessage("Failed to connect to server");
    } finally {
      setAccepting(false);
    }
  }

  return (
    <main style={styles.page}>
      <header style={styles.topbar}>
        <Link href="/homepage" style={styles.brand}>
          ChronoVS
        </Link>
      </header>

      <section style={styles.panel}>
        <div style={styles.avatarRow} aria-hidden="true">
          <div style={{ ...styles.avatar, background: "#7ee787" }}>C</div>
          <div style={{ ...styles.avatar, background: "#ff7b72" }}>V</div>
        </div>

        <h1 style={styles.title}>
          {owner} invited you to collaborate
        </h1>
        <p style={styles.subtitle}>
          {loadingRepo ? "Loading invitation..." : `Accept this invitation to join ${repoName}.`}
        </p>

        <button
          onClick={handleAccept}
          disabled={accepting || accepted}
          style={{
            ...styles.acceptButton,
            ...(accepting || accepted ? styles.disabledButton : {}),
          }}
        >
          {accepted ? "Accepted" : accepting ? "Accepting..." : "Accept invitation"}
        </button>

        {message && (
          <div
            style={{
              ...styles.message,
              ...(accepted ? styles.successMessage : styles.errorMessage),
            }}
          >
            {message}
          </div>
        )}

        <div style={styles.permissions}>
          <div style={styles.permissionsTitle}>After accepting, repository admins can see:</div>
          <ul style={styles.permissionList}>
            <li>Your email address</li>
            <li>Your access level for this repository</li>
            <li>Your activity within this repository</li>
          </ul>
        </div>
      </section>
    </main>
  );
}

const BG = "#f6f8fa";
const TEXT = "#24292f";
const MUTED = "#57606a";
const BORDER = "#d0d7de";
const GREEN = "#2da44e";

const styles: { [key: string]: CSSProperties } = {
  page: {
    minHeight: "100vh",
    background: BG,
    color: TEXT,
    fontFamily: "Arial",
  },
  topbar: {
    height: 56,
    borderBottom: `1px solid ${BORDER}`,
    display: "flex",
    alignItems: "center",
    padding: "0 24px",
    background: "white",
  },
  brand: {
    color: "#0969da",
    fontSize: 18,
    fontWeight: 700,
    textDecoration: "none",
  },
  panel: {
    width: "min(560px, calc(100vw - 32px))",
    margin: "72px auto 0",
    textAlign: "center",
  },
  avatarRow: {
    display: "flex",
    justifyContent: "center",
    gap: 24,
    marginBottom: 18,
  },
  avatar: {
    width: 42,
    height: 42,
    borderRadius: 6,
    color: "white",
    fontWeight: 800,
    fontSize: 22,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  title: {
    margin: "0 0 8px",
    fontSize: 22,
    fontWeight: 500,
  },
  subtitle: {
    margin: "0 0 24px",
    color: MUTED,
    fontSize: 14,
  },
  acceptButton: {
    background: GREEN,
    border: `1px solid ${GREEN}`,
    borderRadius: 6,
    color: "white",
    cursor: "pointer",
    fontSize: 14,
    fontWeight: 700,
    padding: "9px 18px",
  },
  disabledButton: {
    cursor: "wait",
    opacity: 0.7,
  },
  message: {
    margin: "18px auto 0",
    borderRadius: 6,
    padding: "10px 12px",
    fontSize: 13,
    maxWidth: 420,
  },
  successMessage: {
    background: "#dafbe1",
    border: "1px solid #aceebb",
    color: "#1a7f37",
  },
  errorMessage: {
    background: "#ffebe9",
    border: "1px solid #ffcecb",
    color: "#cf222e",
  },
  permissions: {
    margin: "34px auto 0",
    borderTop: `1px solid ${BORDER}`,
    maxWidth: 420,
    paddingTop: 20,
    textAlign: "left",
    color: MUTED,
    fontSize: 14,
  },
  permissionsTitle: {
    color: TEXT,
    marginBottom: 10,
  },
  permissionList: {
    margin: 0,
    paddingLeft: 22,
    lineHeight: 1.65,
  },
};
