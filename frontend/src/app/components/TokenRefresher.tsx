"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";

function getTokenExp(token: string): number | null {
  try {
    // JWT uses base64url — replace chars before decoding
    const base64 = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(atob(base64));
    return typeof payload.exp === "number" ? payload.exp : null;
  } catch {
    return null;
  }
}

export default function TokenRefresher() {
  const router = useRouter();
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    function scheduleRefresh() {
      if (timerRef.current) clearTimeout(timerRef.current);

      const token = localStorage.getItem("token");
      const refreshToken = localStorage.getItem("refresh_token");
      const email = localStorage.getItem("email");

      if (!token || !refreshToken || !email) return;

      const exp = getTokenExp(token);
      if (!exp) return;

      // Refresh 60 seconds before the token actually expires
      const msUntilRefresh = exp * 1000 - Date.now() - 60_000;

      if (msUntilRefresh <= 0) {
        doRefresh(refreshToken, email);
        return;
      }

      timerRef.current = setTimeout(() => doRefresh(refreshToken, email), msUntilRefresh);
    }

    async function doRefresh(refreshToken: string, email: string) {
      try {
        const res = await fetch("/api/auth/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refreshToken, email }),
        });

        if (res.ok) {
          const data = await res.json();
          localStorage.setItem("token", data.access_token);
          scheduleRefresh();
        } else {
          // Refresh token expired — force re-login
          localStorage.removeItem("token");
          localStorage.removeItem("refresh_token");
          localStorage.removeItem("email");
          router.push("/login");
        }
      } catch {
        // Network error — timer will be rescheduled on the next page load
      }
    }

    scheduleRefresh();

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [router]);

  return null;
}
