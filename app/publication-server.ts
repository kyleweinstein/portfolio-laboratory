import type { Environment } from "./github-auth.ts";

const SERVICE_PREFIX = "/v1";
const MAX_RESPONSE_BYTES = 2_000_000;
const SERVICE_TIMEOUT_MS = 15_000;

export type PublicationQuality =
  | "broker_reported"
  | "statement_reconciled"
  | "computed"
  | "estimated"
  | "unavailable";

export type PublishedPerformancePoint = {
  date: string;
  returnPercent: number;
  benchmarkReturnPercent: number | null;
};

export type PortfolioCard = {
  slug: string;
  title: string;
  provider: "Webull" | "M1 Finance" | "Charles Schwab";
  ytdReturnPercent: number | null;
  performanceThrough: string | null;
  quality: PublicationQuality;
  performance: PublishedPerformancePoint[];
};

export type PublishedHolding = {
  kind: "security" | "cash_margin" | "other";
  symbol: string | null;
  name: string;
  weightPercent: number | null;
  costBasisPerShare: number | null;
  returnPercent: number | null;
  quality: PublicationQuality;
};

export type PublishedRiskStatistics = {
  annualReturnPercent: number | null;
  annualVolatilityPercent: number | null;
  sharpeRatio: number | null;
  maximumDrawdownPercent: number | null;
  valueAtRisk95Percent: number | null;
  conditionalValueAtRisk95Percent: number | null;
  beta: number | null;
};

export type PublishedClassification = {
  symbol: string;
  style: string | null;
  sector: string | null;
  factor: string | null;
  confidence: "high" | "moderate" | "low" | "unavailable";
};

export type PublishedPairInsight = {
  leftSymbol: string;
  rightSymbol: string;
  correlation: number | null;
  spreadVolatilityPercent: number | null;
  rebalancePotentialPercent: number | null;
};

export type PublishedAllocation = {
  symbol: string;
  weightPercent: number;
};

export type PublishedCorrelationMap = {
  symbols: string[];
  packedCorrelations: number[];
};

export type PublishedDirectionLane = {
  symbol: string;
  upSharePercent: number | null;
  directions: number[];
};

export type PublishedDirectionComparison = {
  dates: string[];
  lanes: PublishedDirectionLane[];
};

export type PublishedRebalanceBucket = {
  symbols: string[];
  targetWeightsPercent: number[];
  priceImpliedWeightsPercent: number[];
  driftPercent: number | null;
  triggered: boolean;
};

export type PublishedAnalytics = {
  risk: PublishedRiskStatistics | null;
  classifications: PublishedClassification[];
  pairInsights: PublishedPairInsight[];
  optimizedAllocation: PublishedAllocation[];
  correlationMap: PublishedCorrelationMap | null;
  directionComparison: PublishedDirectionComparison | null;
  rebalanceBuckets: PublishedRebalanceBucket[];
  analyticsAsOf: string | null;
  observationCount: number | null;
};

export type PortfolioDetail = PortfolioCard & {
  benchmarkSymbol: string | null;
  holdingsAsOf: string | null;
  grossExposurePercent: number | null;
  netExposurePercent: number | null;
  analyticsSleevePercent: number | null;
  holdings: PublishedHolding[];
  analytics: PublishedAnalytics;
};

export type ManagedPublication = {
  publicationId: string | null;
  accountHandle: string | null;
  slug: string;
  title: string;
  provider: PortfolioCard["provider"];
  published: boolean;
  quality: PublicationQuality;
  performanceThrough: string | null;
  lastSuccessfulSyncAt: string | null;
  issueCount: number;
  benchmarkSymbol: string | null;
};

export type OwnerProviderAccount = {
  accountHandle: string;
  provider: PortfolioCard["provider"];
  accountType: string | null;
  currency: string | null;
  lastSyncedAt: string | null;
};

type ServiceConfig = { baseUrl: URL; internalToken: string; ownerId: string };

export type ProviderCapability = {
  provider: "Webull" | "M1 Finance" | "Charles Schwab";
  enabled: boolean;
  configured: boolean;
  readOnly: boolean;
  holdings: boolean;
  activities: boolean;
  authoritativePerformance: boolean;
  onDemandRefresh: boolean;
  statementAnchors: boolean;
  accountCount: number;
  status: "connected" | "action_required" | "unavailable" | "disabled";
  message: string | null;
};

export class PublicationServiceError extends Error {
  status: number;

  constructor(message: string, status = 502) {
    super(message);
    this.name = "PublicationServiceError";
    this.status = status;
  }
}

function runtimeEnvironment(): Environment {
  return typeof process === "undefined" ? {} : process.env;
}

export async function loadPublishedPortfolioCards(
  env: Environment = runtimeEnvironment(),
): Promise<PortfolioCard[]> {
  const payload = await publicationServiceRequest("/publications", env);
  return normalizePortfolioCards(payload);
}

export async function loadPublishedPortfolioDetail(
  slug: string,
  env: Environment = runtimeEnvironment(),
): Promise<PortfolioDetail | null> {
  if (!isPortfolioSlug(slug)) return null;
  try {
    const payload = await publicationServiceRequest(
      `/publications/${encodeURIComponent(slug)}`,
      env,
    );
    return normalizePortfolioDetail(payload);
  } catch (caught) {
    if (caught instanceof PublicationServiceError && caught.status === 404) {
      return null;
    }
    throw caught;
  }
}

export async function loadManagedPublicationPreview(
  publicationId: string,
  env: Environment = runtimeEnvironment(),
): Promise<PortfolioDetail | null> {
  if (!isOpaquePublicationId(publicationId)) return null;
  try {
    const payload = await publicationServiceRequest(
      `/publications/${encodeURIComponent(publicationId)}/preview`,
      env,
    );
    return normalizePortfolioDetail(payload);
  } catch (caught) {
    if (caught instanceof PublicationServiceError && caught.status === 404) return null;
    throw caught;
  }
}

export async function loadManagedPublications(
  env: Environment = runtimeEnvironment(),
): Promise<ManagedPublication[]> {
  const payload = await publicationServiceRequest("/publications/manage", env);
  const records = extractList(payload);
  return records.flatMap((record) => {
    const slug = slugField(record, "slug");
    const title = publicTitle(record.title);
    const provider = providerFrom(record.provider);
    if (!slug || !title || !provider) return [];
    return [{
      publicationId: opaqueIdentifier(first(record, "publicationId", "publication_id", "id")),
      accountHandle: opaqueIdentifier(first(record, "accountHandle", "account_handle")),
      slug,
      title,
      provider,
      published: record.published === true || (record.enabled === true && record.hasPublishedRevision === true),
      quality: qualityFrom(record.quality),
      performanceThrough: dateField(record, "performanceThrough", "performance_through"),
      lastSuccessfulSyncAt: timestampField(record, "lastSuccessfulSyncAt", "last_successful_sync_at"),
      issueCount: boundedNonnegativeInteger(
        first(record, "issueCount", "issue_count"),
        10_000,
      ) ?? 0,
      benchmarkSymbol: symbolValue(first(record, "benchmarkSymbol", "benchmark_symbol")),
    }];
  });
}

export async function loadProviderAccounts(
  provider: "webull" | "plaid_m1" | "schwab",
  env: Environment = runtimeEnvironment(),
): Promise<OwnerProviderAccount[]> {
  const payload = await publicationServiceRequest(
    `/providers/${provider}/accounts`,
    env,
  );
  const record = unwrapRecord(payload);
  const values = record && Array.isArray(record.accounts) ? record.accounts : [];
  return values.flatMap(value => {
    const account = asRecord(value);
    const accountHandle = account
      ? opaqueIdentifier(first(account, "accountHandle", "account_handle"))
      : null;
    const normalizedProvider = account ? providerFrom(account.provider ?? provider) : null;
    if (!account || !accountHandle || !normalizedProvider) return [];
    return [{
      accountHandle,
      provider: normalizedProvider,
      accountType: publicText(first(account, "accountType", "account_type"), 80),
      currency: typeof account.currency === "string" && /^[A-Z]{3}$/.test(account.currency)
        ? account.currency
        : null,
      lastSyncedAt: timestampValue(first(account, "lastSyncedAt", "last_synced_at")),
    }];
  });
}

export async function publishManagedPublication(
  publicationId: string,
  env: Environment = runtimeEnvironment(),
): Promise<{ published: true }> {
  if (!opaqueIdentifier(publicationId)) {
    throw new PublicationServiceError("The publication is invalid.", 400);
  }
  await publicationServiceRequest(
    `/publications/${encodeURIComponent(publicationId)}/publish`,
    env,
    { method: "POST", body: {} },
  );
  return { published: true };
}

export async function unpublishManagedPublication(
  publicationId: string,
  env: Environment = runtimeEnvironment(),
): Promise<{ published: false }> {
  if (!opaqueIdentifier(publicationId)) {
    throw new PublicationServiceError("The publication is invalid.", 400);
  }
  await publicationServiceRequest(
    `/publications/${encodeURIComponent(publicationId)}`,
    env,
    { method: "DELETE" },
  );
  return { published: false };
}

export async function configureManagedPublication(
  input: {
    accountHandle: string;
    slug: string;
    title: string;
    benchmarkSymbol: string;
    enabled: boolean;
  },
  env: Environment = runtimeEnvironment(),
): Promise<{ configured: true }> {
  if (
    !opaqueIdentifier(input.accountHandle) ||
    !isPortfolioSlug(input.slug) ||
    !publicTitle(input.title) ||
    !symbolValue(input.benchmarkSymbol) ||
    typeof input.enabled !== "boolean"
  ) {
    throw new PublicationServiceError("The publication settings are invalid.", 400);
  }
  await publicationServiceRequest("/publications/configure", env, {
    method: "PUT",
    body: {
      accountHandle: input.accountHandle,
      slug: input.slug,
      title: input.title.trim(),
      benchmarkSymbol: input.benchmarkSymbol.trim().toUpperCase(),
      enabled: input.enabled,
    },
  });
  return { configured: true };
}

export async function loadProviderCapabilities(
  env: Environment = runtimeEnvironment(),
): Promise<ProviderCapability[]> {
  const payload = await publicationServiceRequest("/providers", env);
  const record = unwrapRecord(payload);
  const values = record && Array.isArray(record.providers) ? record.providers : [];
  return values.flatMap((value) => {
    const providerRecord = asRecord(value);
    const provider = providerFrom(providerRecord?.provider);
    if (!providerRecord || !provider) return [];
    const accounts = providerRecord.accounts;
    const status = providerStatusFrom(providerRecord.status, providerRecord.enabled);
    return [{
      provider,
      enabled: providerRecord.enabled === true,
      configured: providerRecord.configured === true,
      readOnly: providerRecord.readOnly === true || providerRecord.read_only === true,
      holdings: providerRecord.positions === true || providerRecord.holdings === true,
      activities: providerRecord.activities === true,
      authoritativePerformance: providerRecord.authoritativePerformance === true || providerRecord.authoritative_performance === true,
      onDemandRefresh: providerRecord.onDemandRefresh === true || providerRecord.on_demand_refresh === true,
      statementAnchors: providerRecord.statementAnchors === true || providerRecord.statement_anchors === true,
      accountCount: Array.isArray(accounts)
        ? Math.min(accounts.length, 1_000)
        : boundedNonnegativeInteger(first(providerRecord, "accountCount", "account_count"), 1_000) ?? 0,
      status,
      message: publicText(providerRecord.message, 240),
    }];
  });
}

export async function createPlaidLinkToken(
  env: Environment = runtimeEnvironment(),
): Promise<{ linkToken: string; expiration: string | null }> {
  const payload = await publicationServiceRequest(
    "/providers/plaid/link-token",
    env,
    { method: "POST", body: {} },
  );
  const record = unwrapRecord(payload);
  const linkToken = record ? privateTokenField(first(record, "linkToken", "link_token")) : null;
  if (!linkToken) throw new PublicationServiceError("Plaid Link could not be started.", 502);
  return {
    linkToken,
    expiration: record ? timestampValue(first(record, "expiration")) : null,
  };
}

export async function exchangePlaidPublicToken(
  publicToken: string,
  env: Environment = runtimeEnvironment(),
): Promise<{ connected: boolean; provider: "M1 Finance"; status: string }> {
  if (!/^public-[A-Za-z0-9_-]{8,512}$/.test(publicToken)) {
    throw new PublicationServiceError("Plaid returned an invalid connection token.", 400);
  }
  const payload = await publicationServiceRequest(
    "/providers/plaid/exchange",
    env,
    { method: "POST", body: { publicToken } },
  );
  const record = unwrapRecord(payload);
  const status = publicText(record?.status, 80) ?? "connected";
  return {
    connected: record?.connected !== false,
    provider: "M1 Finance",
    status,
  };
}

export async function forwardVerifiedPlaidWebhook(
  rawBody: string,
  signature: string,
  env: Environment = runtimeEnvironment(),
): Promise<{ accepted: boolean; syncRequired: boolean }> {
  if (
    rawBody.length < 2 ||
    rawBody.length > 131_072 ||
    signature.length < 16 ||
    signature.length > 4096
  ) {
    throw new PublicationServiceError("The Plaid webhook is invalid.", 400);
  }
  const payload = await publicationServiceRequest(
    "/providers/plaid/webhook",
    env,
    { method: "POST", body: { rawBody, signature } },
  );
  const record = unwrapRecord(payload);
  if (!record || record.accepted !== true) {
    throw new PublicationServiceError("The Plaid webhook was rejected.", 401);
  }
  return {
    accepted: true,
    syncRequired: record.syncRequired === true || record.sync_required === true,
  };
}

export function normalizePortfolioCards(payload: unknown): PortfolioCard[] {
  return extractList(payload)
    .map(normalizePortfolioCard)
    .filter((card): card is PortfolioCard => Boolean(card));
}

export function normalizePortfolioDetail(payload: unknown): PortfolioDetail | null {
  const record = unwrapRecord(payload);
  if (!record) return null;
  const card = normalizePortfolioCard(asRecord(record.card) ?? record);
  if (!card) return null;
  const holdingsValue = first(record, "holdings", "publishedHoldings", "published_holdings");
  const holdings = Array.isArray(holdingsValue)
    ? holdingsValue.flatMap((value) => {
        const holding = normalizeHolding(value);
        return holding ? [holding] : [];
      })
    : [];
  const analyticsRecord = asRecord(first(record, "analytics", "publishedAnalytics", "published_analytics"));
  const detail: PortfolioDetail = {
    ...card,
    benchmarkSymbol: symbolValue(first(record, "benchmarkSymbol", "benchmark_symbol")),
    holdingsAsOf: dateField(record, "holdingsAsOf", "holdings_as_of"),
    grossExposurePercent: percentValue(first(record, "grossExposurePercent", "gross_exposure_percent")),
    netExposurePercent: percentValue(first(record, "netExposurePercent", "net_exposure_percent")),
    analyticsSleevePercent: percentValue(first(
      record,
      "analyticsSleevePercent",
      "analytics_sleeve_percent",
      "analyticalSleevePercent",
      "analytical_sleeve_percent",
    )),
    holdings,
    analytics: normalizeAnalytics(analyticsRecord),
  };
  assertViewerSafeShape(detail);
  return detail;
}

export function assertViewerSafeShape(value: unknown): void {
  visitSafeValue(value, "response", new Set());
}

async function publicationServiceRequest(
  path: string,
  env: Environment,
  options: {
    method?: "GET" | "POST" | "PUT" | "DELETE";
    body?: Record<string, unknown>;
  } = {},
): Promise<unknown> {
  const config = getServiceConfig(env);
  const url = serviceUrl(config.baseUrl, path);
  let response: Response;
  try {
    response = await fetch(url, {
      method: options.method ?? "GET",
      headers: {
        accept: "application/json",
        authorization: `Bearer ${config.internalToken}`,
        "x-portfolio-owner-github-id": config.ownerId,
        ...(options.body ? { "content-type": "application/json" } : {}),
      },
      body: options.body ? JSON.stringify(options.body) : undefined,
      redirect: "error",
      signal: AbortSignal.timeout(SERVICE_TIMEOUT_MS),
    });
  } catch {
    throw new PublicationServiceError(
      "Published portfolios are temporarily unavailable.",
      503,
    );
  }
  const payload = await readLimitedJson(response);
  if (!response.ok) {
    throw new PublicationServiceError(
      response.status === 404
        ? "The published portfolio was not found."
        : "Published portfolios are temporarily unavailable.",
      response.status,
    );
  }
  return payload;
}

function getServiceConfig(env: Environment): ServiceConfig {
  const rawUrl = env.PORTFOLIO_SERVICE_URL?.trim() ??
    env.BROKER_SERVICE_URL?.trim() ??
    env.WEBULL_SERVICE_URL?.trim() ??
    "";
  const internalToken = env.PORTFOLIO_INTERNAL_TOKEN ??
    env.BROKER_INTERNAL_TOKEN ??
    env.WEBULL_INTERNAL_TOKEN ??
    "";
  const ownerId = env.PORTFOLIO_OWNER_GITHUB_ID?.trim() ||
    env.GITHUB_OWNER_ID?.trim() ||
    env.GITHUB_OWNER_IDS?.split(",")[0]?.trim() ||
    "";
  if (!rawUrl || internalToken.length < 16 || !/^[1-9]\d*$/.test(ownerId)) {
    throw new PublicationServiceError(
      "Published portfolios are not configured.",
      503,
    );
  }
  let baseUrl: URL;
  try {
    baseUrl = new URL(rawUrl);
  } catch {
    throw new PublicationServiceError("Published portfolios are not configured.", 503);
  }
  if (
    !["http:", "https:"].includes(baseUrl.protocol) ||
    baseUrl.username ||
    baseUrl.password
  ) {
    throw new PublicationServiceError("Published portfolios are not configured.", 503);
  }
  baseUrl.search = "";
  baseUrl.hash = "";
  if (!baseUrl.pathname.endsWith("/")) baseUrl.pathname += "/";
  return { baseUrl, internalToken, ownerId };
}

function serviceUrl(baseUrl: URL, path: string): URL {
  if (!path.startsWith("/") || path.includes("..")) {
    throw new PublicationServiceError("The publication route is invalid.", 500);
  }
  const url = new URL(`${SERVICE_PREFIX.slice(1)}${path}`, baseUrl);
  if (url.origin !== baseUrl.origin || !url.pathname.startsWith(baseUrl.pathname)) {
    throw new PublicationServiceError("The publication route is invalid.", 500);
  }
  return url;
}

function normalizePortfolioCard(value: unknown): PortfolioCard | null {
  const record = asRecord(value);
  if (!record) return null;
  const slug = slugField(record, "slug");
  const title = publicTitle(record.title);
  const provider = providerFrom(record.provider);
  if (!slug || !title || !provider) return null;
  const performanceValue = first(record, "performance", "performancePoints", "performance_points", "chart");
  const performance = Array.isArray(performanceValue)
    ? performanceValue.flatMap((point) => {
        const normalized = normalizePerformancePoint(point);
        return normalized ? [normalized] : [];
      })
    : [];
  const card: PortfolioCard = {
    slug,
    title,
    provider,
    ytdReturnPercent: percentValue(first(record, "ytdReturnPercent", "ytd_return_percent")),
    performanceThrough: dateField(record, "performanceThrough", "performance_through"),
    quality: qualityFrom(record.quality),
    performance,
  };
  assertViewerSafeShape(card);
  return card;
}

function normalizePerformancePoint(value: unknown): PublishedPerformancePoint | null {
  const record = asRecord(value);
  if (!record) return null;
  const date = dateValue(first(record, "date", "at"));
  const returnPercent = percentValue(first(record, "returnPercent", "return_percent", "portfolioReturnPercent", "portfolio_return_percent"));
  if (!date || returnPercent === null) return null;
  return {
    date,
    returnPercent,
    benchmarkReturnPercent: percentValue(first(record, "benchmarkReturnPercent", "benchmark_return_percent")),
  };
}

function normalizeHolding(value: unknown): PublishedHolding | null {
  const record = asRecord(value);
  if (!record) return null;
  const kind = holdingKindFrom(record.kind);
  if (!kind) return null;
  const symbol = kind === "security" ? symbolValue(record.symbol) : null;
  if (kind === "security" && !symbol) return null;
  const defaultName = kind === "cash_margin" ? "Cash / Margin" : kind === "other" ? "Other assets / liabilities" : symbol!;
  const name = publicText(record.name, 160) ?? defaultName;
  return {
    kind,
    symbol,
    name,
    weightPercent: percentValue(first(record, "weightPercent", "weight_percent")),
    costBasisPerShare: kind === "security"
      ? nonnegativeFinite(first(record, "costBasisPerShare", "cost_basis_per_share"), 1_000_000_000)
      : null,
    returnPercent: kind === "security"
      ? percentValue(first(record, "returnPercent", "return_percent"))
      : null,
    quality: qualityFrom(record.quality),
  };
}

function normalizeAnalytics(record: Record<string, unknown> | null): PublishedAnalytics {
  const riskRecord = asRecord(record?.risk);
  const classificationsValue = record ? first(record, "classifications") : null;
  const pairValue = record ? first(record, "pairInsights", "pair_insights") : null;
  const allocationValue = record ? first(record, "optimizedAllocation", "optimized_allocation") : null;
  const correlationRecord = asRecord(record ? first(record, "correlationMap", "correlation_map") : null);
  const directionRecord = asRecord(record ? first(record, "directionComparison", "direction_comparison") : null);
  const rebalanceValue = record ? first(record, "rebalanceBuckets", "rebalance_buckets") : null;
  return {
    risk: riskRecord ? {
      annualReturnPercent: percentValue(first(riskRecord, "annualReturnPercent", "annual_return_percent")),
      annualVolatilityPercent: percentValue(first(riskRecord, "annualVolatilityPercent", "annual_volatility_percent")),
      sharpeRatio: boundedFinite(first(riskRecord, "sharpeRatio", "sharpe_ratio"), -1_000, 1_000),
      maximumDrawdownPercent: percentValue(first(riskRecord, "maximumDrawdownPercent", "maximum_drawdown_percent")),
      valueAtRisk95Percent: percentValue(first(riskRecord, "valueAtRisk95Percent", "value_at_risk_95_percent")),
      conditionalValueAtRisk95Percent: percentValue(first(riskRecord, "conditionalValueAtRisk95Percent", "conditional_value_at_risk_95_percent")),
      beta: boundedFinite(riskRecord.beta, -1_000, 1_000),
    } : null,
    classifications: Array.isArray(classificationsValue)
      ? classificationsValue.flatMap(normalizeClassification)
      : [],
    pairInsights: Array.isArray(pairValue)
      ? pairValue.flatMap(normalizePairInsight)
      : [],
    optimizedAllocation: Array.isArray(allocationValue)
      ? allocationValue.flatMap(normalizeAllocation)
      : [],
    correlationMap: normalizeCorrelationMap(correlationRecord),
    directionComparison: normalizeDirectionComparison(directionRecord),
    rebalanceBuckets: Array.isArray(rebalanceValue)
      ? rebalanceValue.flatMap(normalizeRebalanceBucket)
      : [],
    analyticsAsOf: record ? dateField(record, "analyticsAsOf", "analytics_as_of") : null,
    observationCount: record
      ? boundedNonnegativeInteger(first(record, "observationCount", "observation_count"), 1_000_000)
      : null,
  };
}

function normalizeCorrelationMap(
  record: Record<string, unknown> | null,
): PublishedCorrelationMap | null {
  if (!record || !Array.isArray(record.symbols)) return null;
  const symbols = record.symbols
    .map(symbolValue)
    .filter((symbol): symbol is string => Boolean(symbol));
  if (symbols.length < 2 || symbols.length > 1_000 || symbols.length !== record.symbols.length) return null;
  const values = first(record, "packedCorrelations", "packed_correlations");
  if (!Array.isArray(values) || values.length !== (symbols.length * (symbols.length + 1)) / 2) return null;
  const packedCorrelations = values.map((value) => boundedFinite(value, -1, 1));
  if (packedCorrelations.some((value) => value === null)) return null;
  return { symbols, packedCorrelations: packedCorrelations as number[] };
}

function normalizeDirectionComparison(
  record: Record<string, unknown> | null,
): PublishedDirectionComparison | null {
  if (!record || !Array.isArray(record.dates) || !Array.isArray(record.lanes)) return null;
  const dates = record.dates.map(dateValue);
  if (!dates.length || dates.length > 1_500 || dates.some((date) => !date)) return null;
  const lanes = record.lanes.slice(0, 6).flatMap((value) => {
    const lane = asRecord(value);
    const symbol = lane ? symbolValue(lane.symbol) : null;
    const directions = lane ? first(lane, "directions") : null;
    if (!symbol || !Array.isArray(directions) || directions.length !== dates.length) return [];
    if (directions.some((direction) => direction !== -1 && direction !== 0 && direction !== 1)) return [];
    return [{
      symbol,
      upSharePercent: percentValue(first(lane!, "upSharePercent", "up_share_percent")),
      directions: directions as number[],
    }];
  });
  return lanes.length >= 2
    ? { dates: dates as string[], lanes }
    : null;
}

function normalizeRebalanceBucket(value: unknown): PublishedRebalanceBucket[] {
  const record = asRecord(value);
  if (!record || !Array.isArray(record.symbols)) return [];
  const symbols = record.symbols.map(symbolValue);
  const targets = first(record, "targetWeightsPercent", "target_weights_percent");
  const implied = first(record, "priceImpliedWeightsPercent", "price_implied_weights_percent");
  if (
    !symbols.length || symbols.some((symbol) => !symbol) ||
    !Array.isArray(targets) || !Array.isArray(implied) ||
    targets.length !== symbols.length || implied.length !== symbols.length
  ) return [];
  const targetWeightsPercent = targets.map(percentValue);
  const priceImpliedWeightsPercent = implied.map(percentValue);
  if (targetWeightsPercent.some((weight) => weight === null) || priceImpliedWeightsPercent.some((weight) => weight === null)) return [];
  return [{
    symbols: symbols as string[],
    targetWeightsPercent: targetWeightsPercent as number[],
    priceImpliedWeightsPercent: priceImpliedWeightsPercent as number[],
    driftPercent: percentValue(first(record, "driftPercent", "drift_percent")),
    triggered: record.triggered === true,
  }];
}

function normalizeClassification(value: unknown): PublishedClassification[] {
  const record = asRecord(value);
  const symbol = record ? symbolValue(record.symbol) : null;
  if (!record || !symbol) return [];
  const confidence = first(record, "confidence");
  return [{
    symbol,
    style: publicText(record.style, 80),
    sector: publicText(record.sector, 80),
    factor: publicText(record.factor, 80),
    confidence: confidence === "high" || confidence === "moderate" || confidence === "low"
      ? confidence
      : "unavailable",
  }];
}

function normalizePairInsight(value: unknown): PublishedPairInsight[] {
  const record = asRecord(value);
  if (!record) return [];
  const leftSymbol = symbolValue(first(record, "leftSymbol", "left_symbol"));
  const rightSymbol = symbolValue(first(record, "rightSymbol", "right_symbol"));
  if (!leftSymbol || !rightSymbol || leftSymbol === rightSymbol) return [];
  return [{
    leftSymbol,
    rightSymbol,
    correlation: boundedFinite(record.correlation, -1, 1),
    spreadVolatilityPercent: percentValue(first(record, "spreadVolatilityPercent", "spread_volatility_percent")),
    rebalancePotentialPercent: percentValue(first(record, "rebalancePotentialPercent", "rebalance_potential_percent")),
  }];
}

function normalizeAllocation(value: unknown): PublishedAllocation[] {
  const record = asRecord(value);
  const symbol = record ? symbolValue(record.symbol) : null;
  const weightPercent = record
    ? percentValue(first(record, "weightPercent", "weight_percent"))
    : null;
  if (!symbol || weightPercent === null || weightPercent < 0) return [];
  return [{ symbol, weightPercent }];
}

function extractList(payload: unknown): Record<string, unknown>[] {
  if (Array.isArray(payload)) return payload.filter(isRecord);
  const record = unwrapRecord(payload);
  if (!record) return [];
  for (const key of ["portfolios", "publications", "items"]) {
    const candidate = record[key];
    if (Array.isArray(candidate)) return candidate.filter(isRecord);
  }
  return [];
}

function unwrapRecord(payload: unknown): Record<string, unknown> | null {
  let current = payload;
  for (let depth = 0; depth < 3; depth += 1) {
    const record = asRecord(current);
    if (!record) return null;
    if (asRecord(record.data)) current = record.data;
    else return record;
  }
  return asRecord(current);
}

function providerFrom(value: unknown): PortfolioCard["provider"] | null {
  if (typeof value !== "string") return null;
  const normalized = value.trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (normalized === "webull") return "Webull";
  if (normalized === "plaid_m1" || normalized === "m1" || normalized === "m1_finance") return "M1 Finance";
  if (normalized === "schwab" || normalized === "charles_schwab") return "Charles Schwab";
  return null;
}

function providerStatusFrom(
  value: unknown,
  enabled: unknown,
): ProviderCapability["status"] {
  if (enabled !== true) return "disabled";
  if (value === "connected" || value === "ready") return "connected";
  if (value === "action_required" || value === "configuration_required") return "action_required";
  if (value === "unavailable") return "unavailable";
  return "unavailable";
}

function qualityFrom(value: unknown): PublicationQuality {
  if (typeof value !== "string") return "unavailable";
  const normalized = value.trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (normalized === "broker_reported") return "broker_reported";
  if (normalized === "statement_reconciled" || normalized === "reconciled") return "statement_reconciled";
  if (normalized === "portfolio_lab_computed" || normalized === "computed") return "computed";
  if (normalized === "estimated") return "estimated";
  return "unavailable";
}

function holdingKindFrom(value: unknown): PublishedHolding["kind"] | null {
  if (value === "security" || value === "cash_margin" || value === "other") return value;
  return null;
}

function percentValue(value: unknown): number | null {
  return boundedFinite(value, -100_000, 100_000);
}

function boundedFinite(value: unknown, minimum: number, maximum: number): number | null {
  const parsed = typeof value === "number"
    ? value
    : typeof value === "string" && value.trim()
      ? Number(value)
      : Number.NaN;
  return Number.isFinite(parsed) && parsed >= minimum && parsed <= maximum
    ? parsed
    : null;
}

function nonnegativeFinite(value: unknown, maximum: number): number | null {
  return boundedFinite(value, 0, maximum);
}

function boundedNonnegativeInteger(value: unknown, maximum: number): number | null {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isSafeInteger(parsed) && parsed >= 0 && parsed <= maximum
    ? parsed
    : null;
}

function symbolValue(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const symbol = value.trim().toUpperCase();
  return /^[A-Z0-9.^=/-]{1,30}$/.test(symbol) ? symbol : null;
}

function slugField(record: Record<string, unknown>, key: string): string | null {
  return typeof record[key] === "string" && isPortfolioSlug(record[key])
    ? record[key]
    : null;
}

export function isPortfolioSlug(value: string): boolean {
  return /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/.test(value);
}

function dateField(record: Record<string, unknown>, ...keys: string[]): string | null {
  return dateValue(first(record, ...keys));
}

function dateValue(value: unknown): string | null {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}(?:T.*)?$/.test(value)) return null;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return null;
  return value.slice(0, 10);
}

function timestampField(record: Record<string, unknown>, ...keys: string[]): string | null {
  const value = first(record, ...keys);
  return typeof value === "string" && Number.isFinite(Date.parse(value)) ? value : null;
}

function publicText(value: unknown, maxLength: number): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim().replace(/\s+/g, " ");
  if (!text || text.length > maxLength || /[\u0000-\u001f\u007f]/.test(text)) return null;
  if (/[$€£¥]/.test(text) || /\b(?:USD|EUR|GBP|CAD)\s*[-+]?\d/i.test(text)) return null;
  return text;
}

function publicTitle(value: unknown): string | null {
  const title = publicText(value, 120);
  if (!title) return null;
  if (/\d{4,}/.test(title) || /\d{1,3}(?:[ ,]\d{3})+(?:\.\d+)?/.test(title)) return null;
  if (/(?:[\u2022*xX#]{2,})\s*[A-Za-z0-9]{3,}/.test(title)) return null;
  if (/\b(?:account|acct|balance|cash|equity|nav|nlv|value|profit|loss|p&l)\b/i.test(title) && /\d/.test(title)) return null;
  return title;
}

function privateTokenField(value: unknown): string | null {
  return typeof value === "string" && value.length >= 8 && value.length <= 4_096
    ? value
    : null;
}

function opaqueIdentifier(value: unknown): string | null {
  return typeof value === "string" && isOpaquePublicationId(value)
    ? value
    : null;
}

export function isOpaquePublicationId(value: string): boolean {
  return /^[A-Za-z0-9_-]{1,100}$/.test(value);
}

function timestampValue(value: unknown): string | null {
  return typeof value === "string" && Number.isFinite(Date.parse(value)) ? value : null;
}

function first(record: Record<string, unknown>, ...keys: string[]): unknown {
  for (const key of keys) {
    if (record[key] !== undefined) return record[key];
  }
  return undefined;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return isRecord(value) ? value : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

// This guard is deliberately semantic rather than an exact-key blocklist.
// Upstream providers use prefixes such as `day`, `total`, and `masked`, so an
// exact match would let aliases like `dayProfitLoss` or `totalMarketValue`
// bypass the public DTO boundary. `costBasisPerShare` is the one intentionally
// published price-like dollar field and must remain an exact spelling.
const FORBIDDEN_VIEWER_KEY_PART = /(?:account|quantity|marketvalue|positionvalue|portfoliovalue|totalvalue|netassetvalue|cash|amount|costbasis|profitloss|pnl|investmentgain|contribution|externalflow|netliquidationvalue|currency|identifier|activity|transaction|execution|deposit|withdrawal)/i;
const FORBIDDEN_VIEWER_ID_KEY = /(?:external|internal|broker|masked|position|activity|transaction)(?:[a-z0-9]*)(?:id|identifier)$/i;
const FORBIDDEN_VIEWER_NAV_KEY = /^(?:nav|accountnav|portfolionav)$/i;

function visitSafeValue(value: unknown, path: string, seen: Set<object>): void {
  if (value === null || value === undefined || typeof value !== "object") return;
  if (seen.has(value as object)) throw new Error("Viewer response cannot be cyclic.");
  seen.add(value as object);
  if (Array.isArray(value)) {
    value.forEach((item, index) => visitSafeValue(item, `${path}[${index}]`, seen));
  } else {
    for (const [key, child] of Object.entries(value)) {
      const normalizedKey = key.replace(/[^a-z0-9]/gi, "").toLowerCase();
      if (
        key !== "costBasisPerShare" &&
        (
          FORBIDDEN_VIEWER_KEY_PART.test(normalizedKey) ||
          (normalizedKey.includes("balance") && !normalizedKey.includes("rebalance")) ||
          FORBIDDEN_VIEWER_ID_KEY.test(normalizedKey) ||
          FORBIDDEN_VIEWER_NAV_KEY.test(normalizedKey)
        )
      ) {
        throw new Error(`Forbidden viewer field at ${path}.${key}.`);
      }
      visitSafeValue(child, `${path}.${key}`, seen);
    }
  }
  seen.delete(value as object);
}

async function readLimitedJson(response: Response): Promise<unknown> {
  const text = await response.text();
  if (text.length > MAX_RESPONSE_BYTES) {
    throw new PublicationServiceError("Published portfolio data is too large.", 502);
  }
  try {
    return JSON.parse(text);
  } catch {
    throw new PublicationServiceError("Published portfolio data is invalid.", 502);
  }
}
