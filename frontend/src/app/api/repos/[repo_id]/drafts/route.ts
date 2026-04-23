import { NextResponse } from "next/server";

// Drafts live on repo-service (port 8003 per the project definition's
// ALB routing table — /v1/repos/*/drafts/* → repo-service).
const REPO_SERVICE_URL =
  process.env.REPO_SERVICE_URL || "http://localhost:8003";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ repo_id: string }> }
) {
  const authHeader = req.headers.get("authorization");
  const { repo_id } = await params;

  try {
    const rawBody = await req.text();
    const response = await fetch(
      `${REPO_SERVICE_URL}/v1/repos/${repo_id}/drafts`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: authHeader || "",
        },
        body: rawBody || "{}",
      }
    );

    const text = await response.text();
    const data = text ? JSON.parse(text) : {};
    return NextResponse.json(data, { status: response.status });
  } catch {
    return NextResponse.json(
      { error: "Failed to connect to backend" },
      { status: 500 }
    );
  }
}

export async function GET(
  req: Request,
  { params }: { params: Promise<{ repo_id: string }> }
) {
  const authHeader = req.headers.get("authorization");
  const { repo_id } = await params;

  try {
    const response = await fetch(
      `${REPO_SERVICE_URL}/v1/repos/${repo_id}/drafts`,
      { headers: { Authorization: authHeader || "" } }
    );

    const text = await response.text();
    const data = text ? JSON.parse(text) : [];
    return NextResponse.json(data, { status: response.status });
  } catch {
    return NextResponse.json(
      { error: "Failed to connect to backend" },
      { status: 500 }
    );
  }
}
