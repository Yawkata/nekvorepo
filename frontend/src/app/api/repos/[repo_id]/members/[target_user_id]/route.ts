import { NextResponse } from "next/server";

const IDENTITY_URL =
  process.env.IDENTITY_SERVICE_URL || "http://localhost:8001";

export async function DELETE(
  req: Request,
  { params }: { params: Promise<{ repo_id: string; target_user_id: string }> }
) {
  const authHeader = req.headers.get("authorization");
  const { repo_id, target_user_id } = await params;

  try {
    const response = await fetch(
      `${IDENTITY_URL}/v1/repos/${repo_id}/members/${encodeURIComponent(
        target_user_id
      )}`,
      {
        method: "DELETE",
        headers: {
          Authorization: authHeader || "",
        },
      }
    );

    if (response.status === 204) {
      return new NextResponse(null, { status: 204 });
    }

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
