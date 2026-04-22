import { NextResponse } from "next/server";

const WORKFLOW_SERVICE_URL =
  process.env.WORKFLOW_SERVICE_URL || "http://localhost:8002";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ repo_id: string; commit_hash: string }> }
) {
  const authHeader = req.headers.get("authorization");
  const { repo_id, commit_hash } = await params;

  try {
    const body = await req.json().catch(() => ({}));

    const response = await fetch(
      `${WORKFLOW_SERVICE_URL}/v1/repos/${repo_id}/commits/${commit_hash}/reject`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: authHeader || "",
        },
        body: JSON.stringify(body),
      }
    );

    if (response.status === 401) {
      return NextResponse.json(
        { error: "Unauthorized" },
        { status: 401 }
      );
    }

    const text = await response.text();
    let data = {};
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        /* ignore parse errors */
      }
    }

    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Reject error:", error);
    return NextResponse.json(
      { error: "Failed to connect to backend" },
      { status: 500 }
    );
  }
}
