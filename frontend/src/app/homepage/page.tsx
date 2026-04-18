"use client";

import { CSSProperties } from "react";
import { useEffect, useState } from "react";
import Link from "next/link";

export default function HomePage() {
  const [repos, setRepos] = useState<any[]>([]);

  useEffect(() => {
  const stored = localStorage.getItem("repos");
  if (stored) {
    setRepos(JSON.parse(stored));
  }
  }, []);
  return (
    <div style={styles.container}>
      
      {/* PURPLE NAVBAR */}
      <div style={styles.navbar}>
        <h3>VSChrono</h3>
        <div style={styles.avatar} />
      </div>

      <div style={styles.content}>
        
        {/* LEFT SIDEBAR */}
        <div style={styles.sidebar}>
          
        </div>

        {/* MAIN */}
        <div style={styles.main}>
          <h2>Home</h2>

          {/* Actions */}
          <div style={styles.actions}>

              <Link href="/create_repo">
                  <button style={styles.button}>
                      Create new repository
                  </button>
              </Link>
          </div>

          {/* SHOW REPOS */}
          {repos.map((repo) => (
              <Link key={repo.repo_id} href={`/repo/${repo.repo_id}`}>
                <div style={styles.card}>
                  <h3>{repo.repo_name}</h3>
                  <p style={{ color: "#8b949e" }}>{repo.description}</p>
                </div>
              </Link>
            ))}

          {/* FOOTER */}
        <div style={styles.footer}>
          <div style={styles.footerLeft}>
            © 2026 VSChrono
          </div>

          <div style={styles.footerRight}>
            <span style={styles.footerLink}>About</span>
            <span style={styles.footerLink}>Docs</span>
            <span style={styles.footerLink}>Contact</span>
          </div>
        </div>
      
      </div>

        {/* RIGHT PANEL */}
        <div style={styles.right}>

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

  search: {
    padding: 6,
    borderRadius: 6,
    border: "none",
    width: 200,
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
    width: 250,
    borderRight: "1px solid purple",
    padding: 15,
  },

  input: {
    width: 220,
    padding: 8,
    margin: "10px 0",
    borderRadius: 6,
    border: "none",
  },

  repoItem: {
    padding: 8,
    borderRadius: 6,
    cursor: "pointer",
  },

  main: {
  flex: 1,
  padding: 20,
  paddingBottom: 70, 
  },
  actions: {
    display: "flex",
    gap: 10,
    marginBottom: 20,
  },

  button: {
    padding: "8px 12px",
    borderRadius: 6,
    border: "1px solid #30363d",
    background: "transparent",
    color: "white",
    cursor: "pointer",
  },

  card: {
    border: "1px solid #30363d",
    borderRadius: 10,
    padding: 15,
    marginBottom: 15,
    background: "#161b22",
  },

  right: {
    width: 280,
    borderLeft: "1px solid purple",
    padding: 15,
  },

  update: {
    marginBottom: 15,
    borderBottom: "1px solid #30363d",
    paddingBottom: 10,
  },

  footer: {
  position: "fixed",
  bottom: 0,
  left: 290,
  width: "66%",
  height: 50,
  background: "#0d1117",
  borderTop: "1px solid purple",
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "0 20px",
  fontSize: 14,
  color: "#8b949e",
  },

  footerLeft: {
  },

  footerRight: {
    display: "flex",
    gap: 15,
  },

  footerLink: {
    cursor: "pointer",
  },
};