import { NextResponse } from "next/server";

const IDENTITY_URL =
  process.env.IDENTITY_SERVICE_URL || "http://localhost:8001";


export async function POST(req: Request) {
  const body = await req.json();

  try {
    const response = await fetch(`${IDENTITY_URL}/v1/auth/refresh`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        refresh_token: body.refresh_token,
        email: body.email,
      }),
    });

    const data = await response.json();

    return NextResponse.json(data, { status: response.status });
  } catch {
    return NextResponse.json(
      { error: "Failed to connect to backend" },
      { status: 500 }
    );
  }
}
