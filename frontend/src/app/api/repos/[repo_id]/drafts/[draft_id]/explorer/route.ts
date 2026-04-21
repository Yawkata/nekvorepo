import { NextResponse } from "next/server";

const REPO_SERVICE_URL =
  process.env.REPO_SERVICE_URL || "http://localhost:8003";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ repo_id: string; draft_id: string }> }
) {
  const authHeader = req.headers.get("authorization");
  const { repo_id, draft_id } = await params;

  try {
    const response = await fetch(
      `${REPO_SERVICE_URL}/v1/repos/${repo_id}/drafts/${draft_id}/explorer`,
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
