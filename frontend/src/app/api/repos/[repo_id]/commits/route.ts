import { NextResponse } from "next/server";

const WORKFLOW_SERVICE_URL =
  process.env.WORKFLOW_SERVICE_URL || "http://localhost:8002";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ repo_id: string }> }
) {
  const authHeader = req.headers.get("authorization");
  const { repo_id } = await params;

  try {
    const response = await fetch(
      `${WORKFLOW_SERVICE_URL}/v1/repos/${repo_id}/commits`,
      {
        headers: { Authorization: authHeader || "" },
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
    console.error("Commits GET error:", error);
    return NextResponse.json(
      { error: "Failed to connect to backend" },
      { status: 500 }
    );
  }
}

export async function POST(
  req: Request,
  { params }: { params: Promise<{ repo_id: string }> }
) {
  const authHeader = req.headers.get("authorization");
  const { repo_id } = await params;

  try {
    const body = await req.json();

    const response = await fetch(
      `${WORKFLOW_SERVICE_URL}/v1/repos/${repo_id}/commits`,
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
    console.error("Commits POST error:", error);
    return NextResponse.json(
      { error: "Failed to connect to backend" },
      { status: 500 }
    );
  }
}
