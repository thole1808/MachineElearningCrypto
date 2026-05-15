import { NextRequest, NextResponse } from "next/server";

const botApiUrl = process.env.BOT_API_URL?.replace(/\/$/, "") || "http://127.0.0.1:8765";

export async function GET(request: NextRequest) {
  const symbol = request.nextUrl.searchParams.get("symbol") || "";
  const query = new URLSearchParams();
  if (symbol) query.set("symbol", symbol);

  try {
    const response = await fetch(`${botApiUrl}/api/bot/pnl?${query.toString()}`, {
      cache: "no-store",
    });
    const body = await response.json();

    return NextResponse.json(body, {
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
