"use client"
import { CSSProperties, useState  } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

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

function Corner({ position }: { position: "top-left" | "bottom-right" }) {
  const containerStyle: CSSProperties = {
    position: "absolute",
    ...(position === "top-left"
      ? { top: 40, left: 60 }
      : { bottom: 40, right: 60 }),
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

        {/* Ring hexes */}
        {tinyPositions.map((pos, i) => (
          <div key={i} style={{ ...hexStyle(100, 100), ...pos }} />
        ))}
      </div>
    </div>
  );
}

export default function Page() {
  const router = useRouter();

  // state for inputs
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");

  // signup handler
  async function handleSignup() {
    const res = await fetch("/api/signup", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },

      // THIS is where stringify is used
      body: JSON.stringify({
        email,
        password,
        name,
      }),
    });

    const data = await res.json();
    console.log("Response:", data);

    if (res.ok) {
      router.push(`/temp_code?email=${encodeURIComponent(email)}`);
    } else {
      alert(data.message || "Signup failed");
    }
  }
  return (
    <>
      {/* Center Form */}
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
        <h2>Sign Up</h2>
        
        <input placeholder="E-Mail" 
        value={email}
          onChange={(e) => setEmail(e.target.value)} 
          style={{ padding: 10, margin: 5, width: 620*0.7}}/>
        <input
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          style={{ padding: 10, margin: 5, width: 620*0.7}}/>

        <input placeholder="Name" 
        value={name}
          onChange={(e) => setName(e.target.value)}
        style={{ padding: 10, margin: 5, width: 620*0.7}} />
        <button onClick={handleSignup} style={{ padding: 10, margin: 5, width: 620*0.74}}>Sign Up</button>

        <Link href="/login" style={{ marginTop: 10, color: "white", textDecoration: "underline" }}>
          Already have an account? Log In
        </Link>
        
      </div>

      {/* Corners */}
      <Corner position="top-left" />
      <Corner position="bottom-right" />
    </>
  );
}