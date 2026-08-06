import { loadMarketSeries, SYMBOL_PATTERN, VALID_YEARS } from "./data";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const symbol = (url.searchParams.get("symbol") || "").trim().toUpperCase();
  const years = Number(url.searchParams.get("years") || "3");
  if (!SYMBOL_PATTERN.test(symbol)) {
    return Response.json({ error: "Enter a valid market symbol." }, { status: 400 });
  }
  if (!VALID_YEARS.has(years)) {
    return Response.json({ error: "History must be 1, 3, or 5 years." }, { status: 400 });
  }

  try {
    return Response.json(await loadMarketSeries(symbol, years), {
      headers: { "cache-control": "public, max-age=900, s-maxage=3600" },
    });
  } catch (caught) {
    const message = caught instanceof Error ? caught.message : `${symbol}: market data source is unavailable`;
    const status = message.includes("insufficient") ? 422 : 502;
    return Response.json({ error: message }, { status });
  }
}
