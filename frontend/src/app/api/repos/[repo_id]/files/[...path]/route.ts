import { NextResponse } from "next/server";

const REPO_SERVICE_URL =
  process.env.REPO_SERVICE_URL || "http://localhost:8003";

export async function GET(
  req: Request,
  {
    params,
  }: {
    params: Promise<{ repo_id: string; path: string[] }>;
  }
) {
  const authHeader = req.headers.get("authorization");
  const { repo_id, path } = await params;
  const url = new URL(req.url);
  const ref = url.searchParams.get("ref");
  const textMode = url.searchParams.get("text") === "1";

  // Reconstruct the file path from path segments
  const filePath = path.map((p) => decodeURIComponent(p)).join("/");
  // Each segment must be URL-encoded individually; the backend uses `path:path`
  // so forward slashes are preserved, but special characters still need encoding.
  const encodedSegments = filePath.split("/").map(encodeURIComponent).join("/");

  try {
    const target =
      `${REPO_SERVICE_URL}/v1/repos/${repo_id}/files/${encodedSegments}` +
      (ref ? `?ref=${encodeURIComponent(ref)}` : "");

    const response = await fetch(target, {
      headers: { Authorization: authHeader || "" },
    });

    if (response.status === 401) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const responseText = await response.text();
    let data: unknown = {};
    if (responseText) {
      try {
        data = JSON.parse(responseText);
      } catch {
        /* ignore parse errors */
      }
    }

    if (textMode && response.ok && data && typeof data === "object" && "url" in data) {
      const downloadUrl =
        typeof (data as { url?: unknown }).url === "string"
          ? (data as { url: string }).url
          : null;

      if (!downloadUrl) {
        return NextResponse.json(
          { error: "Missing file URL for text preview" },
          { status: 500 }
        );
      }

      const fileResponse = await fetch(downloadUrl);
      if (!fileResponse.ok) {
        return NextResponse.json(
          { error: "Failed to load file preview" },
          { status: 502 }
        );
      }

      const body = await fileResponse.text();
      return new NextResponse(body, {
        status: 200,
        headers: {
          "Content-Type": "text/plain; charset=utf-8",
          "Cache-Control": "no-store",
        },
      });
    }

    return NextResponse.json(data, { status: response.status });
  } catch (error) {
    console.error("Files GET error:", error);
    return NextResponse.json(
      { error: "Failed to connect to backend" },
      { status: 500 }
    );
  }
}
