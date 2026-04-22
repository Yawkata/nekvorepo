import { NextResponse } from "next/server";

const REPO_SERVICE_URL =
  process.env.REPO_SERVICE_URL || "http://localhost:8003";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ repo_id: string }> }
) {
  const authHeader = req.headers.get("authorization");
  const { repo_id } = await params;
  const url = new URL(req.url);
  const ref = url.searchParams.get("ref");

  try {
    const target =
      `${REPO_SERVICE_URL}/v1/repos/${repo_id}/view` +
      (ref ? `?ref=${encodeURIComponent(ref)}` : "");

    const response = await fetch(target, {
      headers: { Authorization: authHeader || "" },
    });

    if (response.status === 401) {
      return NextResponse.json(
        { error: "Unauthorized" },
        { status: 401 }
      );
    }

    const text = await response.text();
    let data: unknown = {};
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        /* ignore parse errors */
      }
    }

    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("View GET error:", error);
    return NextResponse.json(
      { error: "Failed to connect to backend" },
      { status: 500 }
    );
  }
}
