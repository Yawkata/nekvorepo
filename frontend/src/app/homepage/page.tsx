"use client";

import { CSSProperties } from "react";

export default function HomePage() {
  return (
    <div style={styles.container}>
      
      {/* PURPLE NAVBAR */}
      <div style={styles.navbar}>
        <h3>MyDashboard</h3>
        <div style={styles.avatar} />
      </div>

      <div style={styles.content}>
        
        {/* LEFT SIDEBAR */}
        <div style={styles.sidebar}>
          <h4>Top repositories</h4>
          <input placeholder="Find a repository..." style={styles.input} />

          {["repo-one", "repo-two", "repo-three"].map((repo) => (
            <div key={repo} style={styles.repoItem}>
              {repo}
            </div>
          ))}
        </div>

        {/* MAIN */}
        <div style={styles.main}>
          <h2>Home</h2>

          {/* Actions */}
          <div style={styles.actions}>
            {["Agent", "Create issue", "Write code", "Git"].map((item) => (
              <button key={item} style={styles.button}>
                {item}
              </button>
            ))}
          </div>

          {/* Feed */}
          <div style={styles.card}>
            <p><strong>User123</strong> followed someone</p>
            <p style={{ color: "#8b949e" }}>last week</p>
          </div>

          <div style={styles.card}>
            <p><strong>Trending repo</strong></p>
            <p style={{ color: "#8b949e" }}>
              AI-powered project example...
            </p>
          </div>
        </div>

        {/* RIGHT PANEL */}
        <div style={styles.right}>
          <h4>Latest updates</h4>

          {["Update 1", "Update 2", "Update 3"].map((item, i) => (
            <div key={i} style={styles.update}>
              <p style={{ fontSize: 12, color: "purple" }}>
                {i + 1} hours ago
              </p>
              <p>{item}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

const styles: { [key: string]: CSSProperties } = {
  container: {
    height: "100vh",
    background: "#0d1117",
    color: "#c9d1d9",
    fontFamily: "Arial",
    display: "flex",
    flexDirection: "column",
  },

  navbar: {
    height: 60,
    background: "purple", // my change
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
    height: "100vh"
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
};