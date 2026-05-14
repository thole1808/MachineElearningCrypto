import { NextRequest, NextResponse } from "next/server";

const botApiUrl = process.env.BOT_API_URL?.replace(/\/$/, "") || "http://127.0.0.1:8765";

export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const symbol = params.get("symbol") || "";
  const interval = params.get("interval") || "1m";
  const limit = params.get("limit") || "120";

  try {
    const query = new URLSearchParams({ interval, limit });
    if (symbol) query.set("symbol", symbol);
    const response = await fetch(`${botApiUrl}/api/binance/klines?${query.toString()}`, {
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
        error: `Gagal membaca candle dari backend ${botApiUrl}. Detail: ${message}`,
      },
      { status: 502 },
    );
  }
}
