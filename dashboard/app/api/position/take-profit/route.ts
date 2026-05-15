import { NextRequest, NextResponse } from "next/server";

const botApiUrl = process.env.BOT_API_URL?.replace(/\/$/, "") || "http://127.0.0.1:8765";

export async function POST(request: NextRequest) {
  try {
    const body = await request.json().catch(() => ({}));
    const response = await fetch(`${botApiUrl}/api/position/take-profit`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    const responseBody = await response.json();

    return NextResponse.json(responseBody, {
      status: response.status,
    });
  } catch (error) {
    const message =
      error instanceof Error
        ? error.message
        : "Backend Python belum berjalan atau tidak bisa dihubungi.";

    return NextResponse.json(
      {
        ok: false,
        error: `Backend belum aktif di ${botApiUrl}. Jalankan ./start-web.sh dari root project. Detail: ${message}`,
      },
      { status: 502 },
    );
  }
}
