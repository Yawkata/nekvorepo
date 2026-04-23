import { NextResponse } from "next/server";

const REPO_SERVICE_URL =
  process.env.REPO_SERVICE_URL || "http://localhost:8003";

export async function DELETE(
  req: Request,
  { params }: { params: Promise<{ repo_id: string; draft_id: string }> }
) {
  const authHeader = req.headers.get("authorization");
  const { repo_id, draft_id } = await params;

  try {
    const response = await fetch(
      `${REPO_SERVICE_URL}/v1/repos/${repo_id}/drafts/${draft_id}`,
      {
        method: "DELETE",
        headers: { Authorization: authHeader || "" },
      }
    );

    if (response.status === 204) {
      return new NextResponse(null, { status: 204 });
    }

    const text = await response.text();
    let data: unknown = {};
    if (text) {
      try {
        data = JSON.parse(text);
      } catch {
        data = { error: text };
      }
    }
    return NextResponse.json(data, { status: response.status });
  } catch {
    return NextResponse.json(
      { error: "Failed to connect to backend" },
      { status: 500 }
    );
  }
}

export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ repo_id: string; draft_id: string }> }
) {
  const authHeader = req.headers.get("authorization");
  const { repo_id, draft_id } = await params;
  const body = await req.text();

  try {
    const response = await fetch(
      `${REPO_SERVICE_URL}/v1/repos/${repo_id}/drafts/${draft_id}`,
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Authorization: authHeader || "",
        },
        body,
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
