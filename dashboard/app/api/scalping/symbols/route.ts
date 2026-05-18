import { NextResponse } from "next/server";

const botApiUrl = process.env.BOT_API_URL?.replace(/\/$/, "") || "http://127.0.0.1:8765";

export async function GET() {
  try {
    const response = await fetch(`${botApiUrl}/api/scalping/symbols`, {
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
        error: `Gagal membaca daftar scalping dari backend ${botApiUrl}. Detail: ${message}`,
      },
      { status: 502 },
    );
  }
}
