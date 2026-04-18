import { NextResponse } from "next/server";

export async function POST(req: Request) {
  const body = await req.json();

  const authHeader = req.headers.get("authorization"); // get token

  try {
    const response = await fetch(
      "http://localhost:8001/v1/repos",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": authHeader || "", // forward it
        },
        body: JSON.stringify({
          repo_name: body.repo_name,
          description: body.description,
        }),
      }
    );

    const text = await response.text(); // safer than .json()

    let data;
    try {
      data = JSON.parse(text);
    } catch {
      return NextResponse.json(
        { error: "Backend did not return JSON", raw: text },
        { status: 500 }
      );
    }

    return NextResponse.json(data, {
      status: response.status,
    });

  } catch (error) {
    console.error("REPO ERROR:", error);

    return NextResponse.json(
      { error: "Failed to connect to backend" },
      { status: 500 }
    );
  }
}