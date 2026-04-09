"use client";

import { useState } from "react";
import { useRouter, useSearchParams  } from "next/navigation";

export default function CodePage() {
  const router = useRouter();
  const params = useSearchParams();

  const email = params.get("email");
  const [code, setCode] = useState("");

  async function handleSubmit() {
    const res = await fetch("/api/temp_code", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },

      // THIS is where stringify is used
      body: JSON.stringify({
        email,
        code,
      }),
    });

    const text = await res.text();
    console.log("Response:", text);

    let data;
    try {
      data = JSON.parse(text);
    } catch {
      alert("Server did not return JSON");
      return;
    }

    if (res.ok) {
      router.push("/homepage");
    } else {
      alert(data.message || "Signup failed");
    }
  }

  return (
    <div
      style={{
        height: "100vh",
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      <div
        style={{
          width: 300,
          height: 200,
          background: "purple",
          borderRadius: 10,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          gap: 15,
          color: "white",
        }}
      >

        {/* Input */}
        <input
          type="text"
          placeholder="Enter the code from your e-mail!"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          style={{
            padding: 10,
            width: "80%",
            borderRadius: 5,
            border: "none",
          }}
        />

        {/* Button */}
        <button
          onClick={handleSubmit}
          style={{
            padding: "8px 16px",
            cursor: "pointer",
          }}
        >
          Submit
        </button>
      </div>
    </div>
  );
}