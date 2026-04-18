"use client";

import { useParams } from "next/navigation";
import { useEffect, useState, CSSProperties } from "react";

export default function RepoPage() {
  const params = useParams();
  const id = params.id;

  const [repo, setRepo] = useState<any>(null);

  useEffect(() => {
    async function fetchRepo() {
      const token = localStorage.getItem("token");

      const res = await fetch(`/api/repo/${id}`, {
        headers: {
          Authorization: `Bearer ${token}`,
        },
      });

      const data = await res.json();

      if (res.ok) {
        setRepo(data);
      }
    }

    fetchRepo();
  }, [id]);

  return (
    <div style={styles.container}>
      
      {/* NAVBAR */}
      <div style={styles.navbar}>
        <h3>VSChrono</h3>
        <div style={styles.avatar} />
      </div>

      {/* MAIN */}
      <div style={styles.main}>
  
      <div style={styles.repoContainer}>

      {/* REPO NAME ABOVE BUTTON */}
      {!repo ? (
        <p style={{ marginBottom: 10 }}></p>
      ) : (
        <h2 style={styles.repoTitle}>{repo?.name || repo?.repo_name || "Untitled repo"}</h2>
      )}
      
      {/* ADD FILE BUTTON */}
      <button style={styles.addButton}>
        Add file
      </button>

      {/* RECTANGLE */}
      <div style={styles.rectangle}>
          <p>Loading...</p>
      </div>

      </div>

      </div>

      {/* FOOTER */}
      <div style={styles.footer}>
        <div>© 2026 VSChrono</div>
        <div style={styles.footerRight}>
          <span style={styles.footerLink}>About</span>
          <span style={styles.footerLink}>Docs</span>
          <span style={styles.footerLink}>Contact</span>
        </div>
      </div>

    </div>
  );
}

const styles: { [key: string]: CSSProperties } = {
  container: {
    minHeight: "100vh",
    background: "#0d1117",
    color: "#c9d1d9",
    fontFamily: "Arial",
    display: "flex",
    flexDirection: "column",
  },

  navbar: {
    height: 60,
    background: "purple",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "0 20px",
    color: "white",
  },

  avatar: {
    width: 30,
    height: 30,
    borderRadius: "50%",
    background: "#ccc",
  },

  main: {
    flex: 1,
    display: "flex",
    justifyContent: "center",
    alignItems: "center",
  },

  repoContainer: {
  width: "70%",
  display: "flex",
  flexDirection: "column",
  },

  repoTitle: {
  marginBottom: 10,
  fontSize: 22,
  fontWeight: "bold",
  },

  addButton: {
  alignSelf: "flex-start",
  marginBottom: 10,
  padding: "8px 12px",
  borderRadius: 6,
  border: "1px solid #30363d",
  background: "purple",
  color: "white",
  cursor: "pointer",
  },

  rectangle: {
    minHeight: "50vh",
    background: "#161b22",
    border: "1px solid #30363d",
    borderRadius: 12,
    padding: 20,
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
  },

  footer: {
    height: 50,
    borderTop: "1px solid purple",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "0 20px",
    fontSize: 14,
    color: "#8b949e",
  },

  footerRight: {
    display: "flex",
    gap: 15,
  },

  footerLink: {
    cursor: "pointer",
  },
};