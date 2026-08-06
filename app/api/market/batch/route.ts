import { loadMarketSeries, mapWithConcurrency, SYMBOL_PATTERN, VALID_YEARS } from "../data";

type BatchRequest = { symbols?: unknown; years?: unknown };

export async function POST(request: Request) {
  let body: BatchRequest;
  try {
    body = await request.json() as BatchRequest;
  } catch {
    return Response.json({ error: "Send a JSON body with symbols and years." }, { status: 400 });
  }

  const years = Number(body.years);
  const symbols = Array.isArray(body.symbols)
    ? [...new Set(body.symbols.map(value => String(value).trim().toUpperCase()).filter(Boolean))]
    : [];
  if (!symbols.length || symbols.length > 150 || symbols.some(symbol => !SYMBOL_PATTERN.test(symbol))) {
    return Response.json({ error: "Provide between 1 and 150 valid market symbols." }, { status: 400 });
  }
  if (!VALID_YEARS.has(years)) {
    return Response.json({ error: "History must be 1, 3, or 5 years." }, { status: 400 });
  }

  const results = await mapWithConcurrency(symbols, 8, async symbol => {
    try {
      return { symbol, data: await loadMarketSeries(symbol, years) } as const;
    } catch (caught) {
      return {
        symbol,
        error: caught instanceof Error ? caught.message : `${symbol}: market data source is unavailable`,
      } as const;
    }
  });
  const dataBySymbol = Object.fromEntries(results.flatMap(result => "data" in result ? [[result.symbol, result.data]] : [])) as Record<string, Awaited<ReturnType<typeof loadMarketSeries>>>;
  const errors = results.flatMap(result => "error" in result ? [{ symbol: result.symbol, message: result.error }] : []);
  const dates = Object.values(dataBySymbol).flatMap(series => series.dates.at(-1) || []);

  return Response.json({
    asOf: dates.sort().at(-1) || null,
    dataBySymbol,
    errors,
  }, { headers: { "cache-control": "private, max-age=0" } });
}
