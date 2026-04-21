"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export default function CreateRepoPage() {
  const router = useRouter();

  const [repoName, setRepoName] = useState("");
  const [description, setDescription] = useState("");

  async function handleCreate() {
  const token = localStorage.getItem("token"); // or wherever you store it

  const res = await fetch("/api/repos", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`, // ✅ ADD THIS
    },
      body: JSON.stringify({
        repo_name: repoName,
        description: description,
      }),
    });

    const data = await res.json();

    if (res.ok) {
      const createdRepo = data;
      const existingRepos =
        JSON.parse(localStorage.getItem("repos") || "[]");

      localStorage.setItem(
      "repos",
      JSON.stringify([...existingRepos, createdRepo])
      );

      router.push("/homepage");
    } else {
      alert(JSON.stringify(data));
    }
  }

  return (
    
    <div
      style={{
        height: "100vh",
        background: "#0d1117",
        color: "white",
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      <div
        style={{
          background: "#161b22",
          padding: 30,
          borderRadius: 10,
          width: 350,
          display: "flex",
          flexDirection: "column",
          gap: 10,
        }}
      >
        <h2>Create Repository</h2>

        <input
          placeholder="Repository name"
          value={repoName}
          onChange={(e) => setRepoName(e.target.value)}
          style={{ padding: 10, borderRadius: 6, border: "none" }}
        />

        <input
          placeholder="Description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          style={{ padding: 10, borderRadius: 6, border: "none" }}
        />

        <button
          onClick={handleCreate}
          style={{
            padding: 10,
            background: "purple",
            color: "white",
            border: "none",
            borderRadius: 6,
            cursor: "pointer",
          }}
        >
          Create
        </button>
      </div>
    </div>
  );
}