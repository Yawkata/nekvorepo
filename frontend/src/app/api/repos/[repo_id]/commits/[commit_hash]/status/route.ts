import { NextResponse } from "next/server";

const WORKFLOW_SERVICE_URL =
  process.env.WORKFLOW_SERVICE_URL || "http://localhost:8002";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ repo_id: string; commit_hash: string }> }
) {
  const authHeader = req.headers.get("authorization");
  const { repo_id, commit_hash } = await params;

  try {
    const response = await fetch(
      `${WORKFLOW_SERVICE_URL}/v1/repos/${repo_id}/commits/${commit_hash}/status`,
      {
        headers: {
          Authorization: authHeader || "",
        },
      }
    );

    const text = await response.text();
    let data: unknown = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { error: text };
    }

    return NextResponse.json(data, { status: response.status });
  } catch {
    return NextResponse.json(
      { error: "Failed to connect to backend" },
      { status: 500 }
    );
  }
}
