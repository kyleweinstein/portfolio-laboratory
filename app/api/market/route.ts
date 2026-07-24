export async function GET(request: Request) {
  const url = new URL(request.url);
  const symbol = (url.searchParams.get("symbol") || "").trim().toUpperCase();
  const years = Number(url.searchParams.get("years") || "3");
  if (!/^[A-Z0-9.^=-]{1,15}$/.test(symbol)) {
    return Response.json({ error: "Enter a valid market symbol." }, { status: 400 });
  }
  if (![1, 3, 5].includes(years)) {
    return Response.json({ error: "History must be 1, 3, or 5 years." }, { status: 400 });
  }

  const period2 = Math.floor(Date.now() / 1000);
  const period1 = period2 - years * 366 * 24 * 60 * 60;
  const sourceUrl = new URL(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}`);
  sourceUrl.searchParams.set("period1", String(period1));
  sourceUrl.searchParams.set("period2", String(period2));
  sourceUrl.searchParams.set("interval", "1d");
  sourceUrl.searchParams.set("events", "history");
  sourceUrl.searchParams.set("includeAdjustedClose", "true");

  try {
    const response = await fetch(sourceUrl, {
      headers: { accept: "application/json", "user-agent": "Portfolio-Laboratory/1.0" },
    });
    if (!response.ok) {
      return Response.json({ error: `${symbol}: source returned ${response.status}` }, { status: 502 });
    }
    const json = await response.json() as any;
    const result = json?.chart?.result?.[0];
    const stamps: number[] = result?.timestamp || [];
    const adjusted: Array<number | null> =
      result?.indicators?.adjclose?.[0]?.adjclose ||
      result?.indicators?.quote?.[0]?.close || [];
    const pairs = stamps
      .map((stamp, index) => [new Date(stamp * 1000).toISOString().slice(0, 10), adjusted[index]] as const)
      .filter((pair): pair is readonly [string, number] => Number.isFinite(pair[1]) && (pair[1] as number) > 0);
    if (pairs.length < 60) {
      return Response.json({ error: `${symbol}: insufficient adjusted-close history` }, { status: 422 });
    }
    return Response.json(
      { symbol, dates: pairs.map(pair => pair[0]), prices: pairs.map(pair => pair[1]), source: "Yahoo Finance chart API" },
      { headers: { "cache-control": "public, max-age=900, s-maxage=3600" } },
    );
  } catch {
    return Response.json({ error: `${symbol}: market data source is unavailable` }, { status: 502 });
  }
}
