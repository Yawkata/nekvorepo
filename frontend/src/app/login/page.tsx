"use client";

import { CSSProperties, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

// HEX STYLE
function hexStyle(width: number, height: number): CSSProperties {
  return {
    position: "absolute",
    width,
    height,
    background: "purple",
    clipPath:
      "polygon(25% 5%, 75% 5%, 100% 50%, 75% 95%, 25% 95%, 0% 50%)",
    display: "flex",
    justifyContent: "center",
    alignItems: "center",
    color: "white",
  };
}

// CORNER HEX RING
function Corner({ position }: { position: "top-right" | "bottom-left" }) {
  const containerStyle: CSSProperties = {
    position: "absolute",
    ...(position === "top-right"
      ? { top: 40, right: 60 }
      : { bottom: 40, left: 60 }),
  };

  const ringStyle: CSSProperties = {
    position: "relative",
    width: 350,
    height: 390,
  };

  const tinyPositions: CSSProperties[] = [
    { top: 0, left: "50%", transform: "translateX(-50%)" },
    { top: "18%", right: 0 },
    { bottom: "18%", right: 0 },
    { bottom: 0, left: "50%", transform: "translateX(-50%)" },
    { bottom: "18%", left: 0 },
    { top: "18%", left: 0 },
  ];

  return (
    <div style={containerStyle}>
      <div style={ringStyle}>
        {/* Center hex */}
        <div
          style={{
            ...hexStyle(180, 180),
            top: "50%",
            left: "50%",
            transform: "translate(-50%, -50%)",
          }}
        />

        {/* Tiny ring hexes */}
        {tinyPositions.map((pos, i) => (
          <div key={i} style={{ ...hexStyle(100, 100), ...pos }} />
        ))}
      </div>
    </div>
  );
}

export default function Page() {
  const router = useRouter();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  async function handleLogin() {
    const res = await fetch("/api/login", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        email,
        password,
      }),
    });

    const data = await res.json();
    console.log("Response:", data);

    if (res.ok) {
      localStorage.setItem("token", data.access_token);
      router.push("/homepage");
    } else {
      alert(data.message || "Login failed");
    }
  }

  return (
    <>
      {/* Center Hex Form */}
      <div
        style={{
          ...hexStyle(620, 620),
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          flexDirection: "column",
          padding: 20,
        }}
      >
        <h2>Login</h2>

        <input
          placeholder="E-Mail"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          style={{ padding: 10, margin: 5, width: 620 * 0.7 }}
        />

        <input
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          style={{ padding: 10, margin: 5, width: 620 * 0.7 }}
        />

        <button
          onClick={handleLogin}
          style={{ padding: 10, margin: 5, width: 620 * 0.74 }}
        >
          Login
        </button>

        <Link href="/" style={{ marginTop: 10, color: "white", textDecoration: "underline" }}>Don't have an account? Sign Up</Link>

      </div>

      {/* Correct corners now */}
      <Corner position="top-right" />
      <Corner position="bottom-left" />
    </>
  );
}