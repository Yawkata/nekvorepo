import { NextResponse } from "next/server";

const REPO_SERVICE_URL =
  process.env.REPO_SERVICE_URL || "http://localhost:8003";

export async function GET(
  req: Request,
  {
    params,
  }: {
    params: Promise<{
      repo_id: string;
      draft_id: string;
      path: string[];
    }>;
  }
) {
  const authHeader = req.headers.get("authorization");
  const { repo_id, draft_id, path } = await params;

  // Join path segments and decode
  const filePath = decodeURIComponent(path.join("/"));

  try {
    const response = await fetch(
      `${REPO_SERVICE_URL}/v1/repos/${repo_id}/drafts/${draft_id}/files/${encodeURIComponent(filePath)}`,
      { headers: { Authorization: authHeader || "" } }
    );

    if (!response.ok) {
      const text = await response.text();
      const data = text ? JSON.parse(text).catch(() => ({})) : {};
      return NextResponse.json(data, { status: response.status });
    }

    const text = await response.text();
    return new NextResponse(text, {
      status: response.status,
      headers: {
        "Content-Type": "text/plain; charset=utf-8",
      },
    });
  } catch {
    return NextResponse.json(
      { error: "Failed to connect to backend" },
      { status: 500 }
    );
  }
}
