import { NextResponse } from "next/server";

export async function GET(
  req: Request,
  { params }: { params: { id: string } }
) {
  const authHeader = req.headers.get("authorization");

  try {
    const response = await fetch(
      `http://localhost:8001/v1/repos/${params.id}`, // ✅ uses ID now
      {
        headers: {
          Authorization: authHeader || "",
        },
      }
    );

    const data = await response.json();

    return NextResponse.json(data, {
      status: response.status,
    });
  } catch (err) {
    return NextResponse.json(
      { error: "Failed to fetch repo" },
      { status: 500 }
    );
  }
}