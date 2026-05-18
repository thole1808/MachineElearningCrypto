import { NextRequest, NextResponse } from "next/server";

const botApiUrl = process.env.BOT_API_URL?.replace(/\/$/, "") || "http://127.0.0.1:8765";

export async function GET(request: NextRequest) {
  const symbol = request.nextUrl.searchParams.get("symbol") || "";
  const symbols = request.nextUrl.searchParams.get("symbols") || "";

  if (symbols) {
    const symbolList = symbols
      .split(",")
      .map((item) => item.trim().toUpperCase())
      .filter(Boolean);

    try {
      const signals = [];
      for (const item of symbolList) {
        const query = new URLSearchParams({ symbol: item });
        const response = await fetch(`${botApiUrl}/api/auto-signal?${query.toString()}`, {
          cache: "no-store",
        });
        const body = await response.json();
        if (!response.ok || !body.ok) {
          signals.push({ symbol: item, ok: false, error: body.error || "Gagal membaca signal." });
        } else {
          signals.push({ ok: true, signal: body.signal });
        }
      }

      return NextResponse.json({ ok: true, signals });
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

  const query = new URLSearchParams();
  if (symbol) query.set("symbol", symbol);

  try {
    const response = await fetch(`${botApiUrl}/api/auto-signal?${query.toString()}`, {
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

export async function POST(request: NextRequest) {
  try {
    const body = await request.json().catch(() => ({}));
    const response = await fetch(`${botApiUrl}/api/auto-signal/trade`, {
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
