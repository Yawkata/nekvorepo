import { NextResponse } from "next/server";

export async function PUT(
  req: Request,
  { params }: { params: Promise<{ repo_id: string; target_user_id: string }> }
) {
  const authHeader = req.headers.get("authorization");
  const { repo_id, target_user_id } = await params;
  const body = await req.json();

  try {
    const response = await fetch(
      `http://localhost:8001/v1/repos/${repo_id}/members/${encodeURIComponent(
        target_user_id
      )}/role`,
      {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          Authorization: authHeader || "",
        },
        body: JSON.stringify({
          role: body.role,
        }),
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
