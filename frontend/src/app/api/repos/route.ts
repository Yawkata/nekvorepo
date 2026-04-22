import { NextResponse } from "next/server";

const IDENTITY_URL =
  process.env.IDENTITY_SERVICE_URL || "http://localhost:8001";


export async function GET(req: Request) {
  const authHeader = req.headers.get("authorization");

  try {
    const response = await fetch(`${IDENTITY_URL}/v1/repos`, {
      headers: {
        Authorization: authHeader || "",
      },
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

export async function POST(req: Request) {
  const body = await req.json();
  const authHeader = req.headers.get("authorization");

  try {
    const response = await fetch(`${IDENTITY_URL}/v1/repos`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: authHeader || "",
      },
      body: JSON.stringify({
        repo_name: body.repo_name,
        description: body.description,
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
