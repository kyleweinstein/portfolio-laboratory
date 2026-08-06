export type MarketSeriesPayload = {
  symbol: string;
  dates: string[];
  prices: number[];
  source: string;
};

type YahooChartPayload = {
  chart?: {
    result?: Array<{
      timestamp?: number[];
      indicators?: {
        adjclose?: Array<{ adjclose?: Array<number | null> }>;
        quote?: Array<{ close?: Array<number | null> }>;
      };
    }>;
  };
};

const CACHE_TTL_MS = 60 * 60 * 1000;
const cache = new Map<string, { expires: number; value: MarketSeriesPayload }>();
const inFlight = new Map<string, Promise<MarketSeriesPayload>>();

export const SYMBOL_PATTERN = /^[A-Z0-9.^=-]{1,15}$/;
export const VALID_YEARS = new Set([1, 3, 5]);

export async function loadMarketSeries(symbol: string, years: number): Promise<MarketSeriesPayload> {
  const key = `${symbol}:${years}`;
  const cached = cache.get(key);
  if (cached && cached.expires > Date.now()) return cached.value;
  const pending = inFlight.get(key);
  if (pending) return pending;

  const request = (async () => {
    const period2 = Math.floor(Date.now() / 1000);
    const period1 = period2 - years * 366 * 24 * 60 * 60;
    const sourceUrl = new URL(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}`);
    sourceUrl.searchParams.set("period1", String(period1));
    sourceUrl.searchParams.set("period2", String(period2));
    sourceUrl.searchParams.set("interval", "1d");
    sourceUrl.searchParams.set("events", "history");
    sourceUrl.searchParams.set("includeAdjustedClose", "true");

    const response = await fetch(sourceUrl, {
      headers: { accept: "application/json", "user-agent": "Portfolio-Laboratory/1.0" },
    });
    if (!response.ok) throw new Error(`${symbol}: source returned ${response.status}`);
    const json = await response.json() as YahooChartPayload;
    const result = json?.chart?.result?.[0];
    const stamps = result?.timestamp || [];
    const adjusted = result?.indicators?.adjclose?.[0]?.adjclose || result?.indicators?.quote?.[0]?.close || [];
    const pairs = stamps
      .map((stamp, index) => [new Date(stamp * 1000).toISOString().slice(0, 10), adjusted[index]] as const)
      .filter((pair): pair is readonly [string, number] => Number.isFinite(pair[1]) && (pair[1] as number) > 0);
    if (pairs.length < 60) throw new Error(`${symbol}: insufficient adjusted-close history`);

    const value: MarketSeriesPayload = {
      symbol,
      dates: pairs.map(pair => pair[0]),
      prices: pairs.map(pair => pair[1]),
      source: "Yahoo Finance chart API",
    };
    cache.set(key, { expires: Date.now() + CACHE_TTL_MS, value });
    return value;
  })().finally(() => inFlight.delete(key));

  inFlight.set(key, request);
  return request;
}

export async function mapWithConcurrency<T, Result>(
  items: T[],
  limit: number,
  mapper: (item: T) => Promise<Result>,
) {
  const results = new Array<Result>(items.length);
  let nextIndex = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (nextIndex < items.length) {
      const index = nextIndex++;
      results[index] = await mapper(items[index]);
    }
  });
  await Promise.all(workers);
  return results;
}
