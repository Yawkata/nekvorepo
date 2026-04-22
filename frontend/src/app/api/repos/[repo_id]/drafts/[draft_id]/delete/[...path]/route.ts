import { NextResponse } from "next/server";

const REPO_SERVICE_URL =
  process.env.REPO_SERVICE_URL || "http://localhost:8003";

export async function DELETE(
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
      {
        method: "DELETE",
        headers: { Authorization: authHeader || "" },
      }
    );

    // Handle 204 No Content (success with no body)
    if (response.status === 204) {
      return new NextResponse(null, { status: 204 });
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
    console.error("Delete route error:", error);
    return NextResponse.json(
      { error: "Failed to connect to backend" },
      { status: 500 }
    );
  }
}
