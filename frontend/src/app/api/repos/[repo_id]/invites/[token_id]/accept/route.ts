import { NextResponse } from "next/server";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ repo_id: string; token_id: string }> }
) {
  const authHeader = req.headers.get("authorization");
  const { repo_id, token_id } = await params;

  try {
    const response = await fetch(
      `http://localhost:8001/v1/repos/${repo_id}/invites/${token_id}/accept`,
      {
        method: "POST",
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
