import { NextResponse } from "next/server";

const REPO_SERVICE_URL =
  process.env.REPO_SERVICE_URL || "http://localhost:8003";

// Streams the multipart body through to repo-service unchanged.
export async function POST(
  req: Request,
  { params }: { params: Promise<{ repo_id: string; draft_id: string }> }
) {
  const authHeader = req.headers.get("authorization");
  const contentType = req.headers.get("content-type") || "";
  const { repo_id, draft_id } = await params;

  try {
    const buf = await req.arrayBuffer();
    const response = await fetch(
      `${REPO_SERVICE_URL}/v1/repos/${repo_id}/drafts/${draft_id}/upload`,
      {
        method: "POST",
        headers: {
          "Content-Type": contentType,
          Authorization: authHeader || "",
        },
        body: Buffer.from(buf),
      }
    );
    const text = await response.text();
    let data: unknown = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      data = { raw: text };
    }
    return NextResponse.json(data, { status: response.status });
  } catch {
    return NextResponse.json(
      { error: "Failed to connect to backend" },
      { status: 500 }
    );
  }
}
