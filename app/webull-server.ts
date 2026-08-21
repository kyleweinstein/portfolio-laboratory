import {
  type Environment,
  type GitHubSession,
  readGitHubSession,
  validateMutationRequest,
} from "./github-auth.ts";
import { loadMarketSeries, type MarketSeriesPayload } from "./api/market/data.ts";

const MAX_UPSTREAM_RESPONSE_BYTES = 2_000_000;
const DEFAULT_UPSTREAM_TIMEOUT_MS = 20_000;
const SERVICE_PREFIX = "/v1";

type ServiceConfig = {
  baseUrl: URL;
  internalToken: string;
};

type ServiceRequest = {
  method?: "GET" | "POST" | "DELETE";
  body?: Record<string, unknown> | null;
  timeoutMs?: number;
};

type ServiceResult = {
  ok: boolean;
  status: number;
  data: unknown;
};

export type OwnerAccess =
  | { ok: true; session: GitHubSession }
  | { ok: false; response: Response };

export class RequestBodyError extends Error {
  status: number;

  constructor(message: string, status = 400) {
    super(message);
    this.name = "RequestBodyError";
    this.status = status;
  }
}

class WebullServiceConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WebullServiceConfigurationError";
  }
}

function runtimeEnvironment(): Environment {
  return typeof process === "undefined" ? {} : process.env;
}

export function isWebullIntegrationEnabled(
  env: Environment = runtimeEnvironment(),
): boolean {
  return env.WEBULL_INTEGRATION_ENABLED?.trim().toLowerCase() === "true";
}

export async function authorizeWebullOwner(
  request: Request,
  options: { mutation?: boolean; env?: Environment } = {},
): Promise<OwnerAccess> {
  const env = options.env ?? runtimeEnvironment();
  if (!isWebullIntegrationEnabled(env)) {
    return {
      ok: false,
      response: jsonResponse(
        { error: "The Webull integration is disabled." },
        404,
      ),
    };
  }

  const session = await readGitHubSession(request, env);
  if (!session) {
    return {
      ok: false,
      response: jsonResponse(
        { error: "Owner authentication is required." },
        401,
      ),
    };
  }

  if (options.mutation) {
    const validation = validateMutationRequest(request, session);
    if (!validation.ok) {
      return {
        ok: false,
        response: jsonResponse({ error: validation.error }, 403),
      };
    }
  }

  return { ok: true, session };
}

export async function webullStatusResponse(
  request: Request,
  env: Environment = runtimeEnvironment(),
): Promise<Response> {
  const base = {
    enabled: isWebullIntegrationEnabled(env),
    authenticated: false,
    connected: false,
    verificationInProgress: false,
    accounts: [] as unknown[],
    selectedAccountId: null as string | null,
    dashboard: null as unknown,
  };
  if (!base.enabled) return jsonResponse(base);

  const session = await readGitHubSession(request, env);
  if (!session) return jsonResponse(base);

  const authenticatedBase = {
    ...base,
    authenticated: true,
    csrfToken: session.csrfToken,
  };

  let serviceStatus: ServiceResult;
  try {
    serviceStatus = await callWebullService(
      "/status",
      session,
      { method: "GET" },
      env,
    );
  } catch (caught) {
    return jsonResponse(
      {
        ...authenticatedBase,
        error: safeServiceFailure(caught),
      },
      502,
    );
  }

  if (!serviceStatus.ok) {
    return jsonResponse(
      {
        ...authenticatedBase,
        error: serviceErrorMessage(serviceStatus),
      },
      502,
    );
  }

  const status = asRecord(serviceStatus.data);
  const connected = status?.connected === true;
  let accounts = Array.isArray(status?.accounts)
    ? normalizeServiceAccounts(status.accounts)
    : null;
  let serviceDashboard = status && "dashboard" in status ? status.dashboard : null;

  if (!accounts) {
    const result = await optionalServiceCall("/accounts", session, env);
    if (result?.ok) {
      const value = asRecord(result.data);
      const candidate = Array.isArray(value?.accounts) ? value.accounts : result.data;
      accounts = Array.isArray(candidate)
        ? normalizeServiceAccounts(candidate)
        : [];
    }
  }

  if (connected && serviceDashboard === null) {
    const [portfolio, activities, issues] = await Promise.all([
      optionalServiceCall("/portfolio", session, env),
      optionalServiceCall("/activities?limit=25", session, env),
      optionalServiceCall("/issues", session, env),
    ]);
    serviceDashboard = {
      portfolio: portfolio?.ok ? redactSensitiveFields(portfolio.data) : null,
      recentActivities: activities?.ok ? activitiesFromService(activities.data) : [],
      issues: issues?.ok ? redactSensitiveFields(issues.data) : null,
    };
  }

  const benchmarkSymbol = configuredBenchmark(env);
  const benchmarkSeries = await loadBenchmarkForDashboard(serviceDashboard, benchmarkSymbol);
  const dashboard = normalizeServiceDashboard(
    serviceDashboard,
    nullableString(status?.lastSyncedAt),
    benchmarkSymbol,
    benchmarkSeries,
  );

  return jsonResponse({
    ...authenticatedBase,
    connected,
    verificationInProgress: status?.verificationInProgress === true,
    accounts: accounts ?? [],
    selectedAccountId: nullableString(status?.selectedAccountId),
    dashboard,
  });
}

export async function proxyWebullJson(
  path: string,
  session: GitHubSession,
  options: ServiceRequest = {},
  env: Environment = runtimeEnvironment(),
): Promise<Response> {
  let result: ServiceResult;
  try {
    result = await callWebullService(path, session, options, env);
  } catch (caught) {
    return jsonResponse({ error: safeServiceFailure(caught) }, 502);
  }

  if (!result.ok) {
    const status = result.status >= 400 && result.status < 500
      ? result.status
      : 502;
    return jsonResponse({ error: serviceErrorMessage(result) }, status);
  }
  return jsonResponse(redactSensitiveFields(result.data), result.status);
}

export async function readJsonBody(
  request: Request,
  options: { required?: boolean; maxBytes?: number } = {},
): Promise<Record<string, unknown>> {
  const maxBytes = options.maxBytes ?? 16_384;
  const declaredLength = Number(request.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > maxBytes) {
    throw new RequestBodyError("The request body is too large.", 413);
  }
  const text = await request.text();
  if (!text) {
    if (options.required) {
      throw new RequestBodyError("A JSON request body is required.");
    }
    return {};
  }
  if (new TextEncoder().encode(text).byteLength > maxBytes) {
    throw new RequestBodyError("The request body is too large.", 413);
  }
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new RequestBodyError("Send a valid JSON request body.");
  }
  const body = asRecord(value);
  if (!body) throw new RequestBodyError("The JSON body must be an object.");
  return body;
}

export function requestBodyErrorResponse(caught: unknown): Response {
  if (caught instanceof RequestBodyError) {
    return jsonResponse({ error: caught.message }, caught.status);
  }
  return jsonResponse({ error: "The request body could not be read." }, 400);
}

export function jsonResponse(body: unknown, status = 200): Response {
  return Response.json(body, {
    status,
    headers: {
      "cache-control": "no-store, max-age=0",
      "cross-origin-resource-policy": "same-origin",
      "referrer-policy": "no-referrer",
      vary: "Cookie",
      "x-content-type-options": "nosniff",
    },
  });
}

async function callWebullService(
  path: string,
  session: GitHubSession,
  options: ServiceRequest,
  env: Environment,
): Promise<ServiceResult> {
  const config = getServiceConfig(env);
  const url = serviceUrl(config.baseUrl, path);
  const method = options.method ?? "GET";
  const headers = new Headers({
    accept: "application/json",
    authorization: `Bearer ${config.internalToken}`,
    "x-portfolio-owner-github-id": session.githubId,
    "x-request-id": crypto.randomUUID(),
  });
  let body: string | undefined;
  if (options.body !== undefined && options.body !== null) {
    headers.set("content-type", "application/json");
    body = JSON.stringify(options.body);
  }

  const response = await fetch(url, {
    method,
    headers,
    body,
    redirect: "manual",
    signal: AbortSignal.timeout(
      options.timeoutMs ?? DEFAULT_UPSTREAM_TIMEOUT_MS,
    ),
  });
  if (response.status >= 300 && response.status < 400) {
    throw new Error("The Webull service returned an unsafe redirect.");
  }
  const declaredLength = Number(response.headers.get("content-length"));
  if (
    Number.isFinite(declaredLength) &&
    declaredLength > MAX_UPSTREAM_RESPONSE_BYTES
  ) {
    throw new Error("The Webull service response was too large.");
  }
  const text = await response.text();
  if (new TextEncoder().encode(text).byteLength > MAX_UPSTREAM_RESPONSE_BYTES) {
    throw new Error("The Webull service response was too large.");
  }
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text) as unknown;
    } catch {
      data = response.ok
        ? { value: text.slice(0, 500) }
        : { error: "The Webull service returned an invalid response." };
    }
  }
  return { ok: response.ok, status: response.status, data };
}

async function optionalServiceCall(
  path: string,
  session: GitHubSession,
  env: Environment,
): Promise<ServiceResult | null> {
  try {
    return await callWebullService(path, session, { method: "GET" }, env);
  } catch {
    return null;
  }
}

function getServiceConfig(env: Environment): ServiceConfig {
  const rawUrl = env.WEBULL_SERVICE_URL?.trim() ?? "";
  const internalToken = env.WEBULL_INTERNAL_TOKEN ?? "";
  if (!rawUrl || internalToken.length < 16) {
    throw new WebullServiceConfigurationError(
      "The Webull service is not configured.",
    );
  }
  let baseUrl: URL;
  try {
    baseUrl = new URL(rawUrl);
  } catch {
    throw new WebullServiceConfigurationError(
      "The Webull service URL is invalid.",
    );
  }
  if (
    !["http:", "https:"].includes(baseUrl.protocol) ||
    baseUrl.username ||
    baseUrl.password
  ) {
    throw new WebullServiceConfigurationError(
      "The Webull service URL is invalid.",
    );
  }
  baseUrl.search = "";
  baseUrl.hash = "";
  if (!baseUrl.pathname.endsWith("/")) baseUrl.pathname += "/";
  return { baseUrl, internalToken };
}

function serviceUrl(baseUrl: URL, path: string): URL {
  if (!/^\/[A-Za-z0-9/_-]*(?:\?[A-Za-z0-9_.~%=&,+:-]*)?$/.test(path)) {
    throw new Error("The Webull service path is invalid.");
  }
  const url = new URL(`${SERVICE_PREFIX.slice(1)}${path}`, baseUrl);
  if (url.origin !== baseUrl.origin || !url.pathname.startsWith(baseUrl.pathname)) {
    throw new Error("The Webull service path escaped its configured base URL.");
  }
  return url;
}

function safeServiceFailure(caught: unknown): string {
  return caught instanceof WebullServiceConfigurationError
    ? caught.message
    : "The Webull service is temporarily unavailable.";
}

function serviceErrorMessage(result: ServiceResult): string {
  const data = asRecord(result.data);
  if (result.status >= 400 && result.status < 500) {
    const candidate = data?.error ?? data?.message ?? data?.detail;
    if (typeof candidate === "string" && candidate.length > 0) {
      return candidate.slice(0, 300);
    }
    if (Array.isArray(data?.detail)) {
      const validation = data.detail
        .map((item) => nullableString(asRecord(item)?.msg))
        .filter((message): message is string => Boolean(message));
      if (validation.length > 0) {
        return `Invalid request: ${validation.join("; ")}`.slice(0, 300);
      }
    }
  }
  return "The Webull service could not complete the request.";
}

function normalizeServiceAccounts(value: unknown[]): Record<string, unknown>[] {
  return value.flatMap((item) => {
    const account = asRecord(item);
    const accountId = nullableString(account?.accountId);
    if (!accountId) return [];
    const accountType = nullableString(account?.accountType) || "Brokerage";
    return [{
      accountId,
      label: `Webull ${accountType.toLowerCase() === "unknown" ? "account" : accountType.toLowerCase()}`,
      maskedIdentifier: `••••${accountId.slice(-4)}`,
      accountType,
      currency: nullableString(account?.currency) || "USD",
    }];
  });
}

function activitiesFromService(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  const record = asRecord(value);
  return Array.isArray(record?.activities) ? record.activities : [];
}

function configuredBenchmark(env: Environment): string {
  const value = (env.WEBULL_BENCHMARK_SYMBOL || "SPY").trim().toUpperCase();
  return /^[A-Z0-9.^=-]{1,15}$/.test(value) ? value : "SPY";
}

async function loadBenchmarkForDashboard(
  value: unknown,
  symbol: string,
): Promise<MarketSeriesPayload | null> {
  const dashboard = asRecord(value);
  const performance = asRecord(dashboard?.performance);
  const periods = recordArray(performance?.periods);
  if (!periods.length) return null;
  const firstDate = dateValue(periods[0]?.start);
  const lastDate = dateValue(periods.at(-1)?.end);
  if (!firstDate || !lastDate) return null;
  const elapsedYears = Math.max(0, (Date.parse(lastDate) - Date.parse(firstDate)) / 31_557_600_000);
  const years = elapsedYears > 3 ? 5 : elapsedYears > 1 ? 3 : 1;
  try {
    return await loadMarketSeries(symbol, years);
  } catch {
    return null;
  }
}

export function normalizeServiceDashboard(
  value: unknown,
  lastSyncedAt: string | null,
  benchmarkSymbol = "SPY",
  benchmarkSeries: MarketSeriesPayload | null = null,
): Record<string, unknown> | null {
  const dashboard = asRecord(value);
  const portfolio = asRecord(dashboard?.portfolio);
  const account = asRecord(portfolio?.account);
  const balance = asRecord(portfolio?.balance);
  const accountId = nullableString(account?.accountId) || nullableString(balance?.accountId);
  if (!dashboard || !accountId || !balance) return null;

  const asOf = dateValue(balance.asOf) || lastSyncedAt;
  const currency = nullableString(balance.currency) || nullableString(account?.currency) || "USD";
  const equity = numberValue(balance.equity);
  const positions = recordArray(portfolio?.positions);
  const eligibleMarketValue = positions.reduce((sum, position) => {
    return isEligiblePosition(position) ? sum + Math.max(0, numberValue(position.marketValue) ?? 0) : sum;
  }, 0);
  const analyticsCoverage = equity && equity !== 0 ? eligibleMarketValue / equity : null;

  const performance = asRecord(dashboard.performance);
  const periods = recordArray(performance?.periods);
  const start = dateValue(performance?.start);
  const end = dateValue(performance?.end);
  const timeWeightedReturn = numberValue(performance?.timeWeightedReturn);
  const moneyWeightedReturn = numberValue(performance?.moneyWeightedReturn);
  const netExternalFlow = numberValue(performance?.netExternalFlow);
  const beginningValue = numberValue(performance?.beginningValue);
  const endingValue = numberValue(performance?.endingValue);
  const investmentGain = beginningValue !== null && endingValue !== null && netExternalFlow !== null
    ? endingValue - beginningValue - netExternalFlow
    : null;
  const historicalQuality = nullableString(performance?.quality) || (periods.length ? "estimated" : "unavailable");
  const historicalThrough = end || null;

  const chart = buildPerformanceChart(periods, benchmarkSeries);
  const benchmarkReturn = chart.benchmarkReturn;
  const excessReturn = timeWeightedReturn !== null && benchmarkReturn !== null && benchmarkReturn > -1
    ? (1 + timeWeightedReturn) / (1 + benchmarkReturn) - 1
    : null;
  const currentMetric = (metricValue: unknown, label: string) => sourcedMetric(
    metricValue,
    label,
    "Webull reported",
    "verified",
    asOf,
    asOf,
    "Point-in-time value returned by Webull OpenAPI.",
    "currency",
    currency,
  );
  const performanceMetric = (metricValue: unknown, label: string, unit: "currency" | "percent") => sourcedMetric(
    metricValue,
    label,
    "Portfolio Lab computed",
    historicalQuality,
    null,
    historicalThrough,
    "Daily cash-flow-adjusted Modified Dietz returns geometrically linked from reconciled account-value observations.",
    unit,
    currency,
  );

  const holdings = positions.map((position, index) => {
    const eligible = isEligiblePosition(position);
    const marketValue = numberValue(position.marketValue);
    const instrumentType = nullableString(position.instrumentType) || "UNKNOWN";
    return {
      positionId: nullableString(position.externalPositionId) || `${accountId}-${index}`,
      symbol: nullableString(position.symbol) || "UNKNOWN",
      instrumentType,
      quantity: numberValue(position.quantity),
      marketValue,
      weight: marketValue !== null && equity && equity !== 0 ? marketValue / equity : null,
      currency: nullableString(position.currency) || currency,
      costBasis: numberValue(position.costBasis),
      unrealizedProfitLoss: numberValue(position.unrealizedProfitLoss),
      eligibleForAnalysis: eligible,
      exclusionReason: eligible ? null : exclusionReason(position),
      source: "Webull reported",
      quality: "verified",
      asOf,
    };
  });
  const exclusions = holdings.filter((holding) => !holding.eligibleForAnalysis).map((holding) => ({
    symbol: holding.symbol,
    instrumentType: holding.instrumentType,
    marketValue: holding.marketValue,
    currency: holding.currency,
    reason: holding.exclusionReason,
  }));
  const activities = recordArray(dashboard.recentActivities).map((activity, index) => ({
    activityId: nullableString(activity.externalActivityId) || `${accountId}-activity-${index}`,
    date: dateValue(activity.occurredAt) || asOf || "",
    type: nullableString(activity.activityType) || "OTHER",
    description: nullableString(activity.description),
    amount: numberValue(activity.amount),
    currency: nullableString(activity.currency) || currency,
    status: nullableString(activity.status) || "Posted",
    source: "Webull reported",
    quality: "verified",
    asOf: dateValue(activity.occurredAt),
  }));
  const issues = recordArray(dashboard.issues).map((issue, index) => {
    const code = nullableString(issue.code) || `DATA_ISSUE_${index + 1}`;
    return {
      issueId: code,
      severity: nullableString(issue.severity) || "info",
      title: humanizeCode(code),
      message: nullableString(issue.message),
    };
  });
  if (periods.length && !benchmarkSeries) {
    issues.push({
      issueId: "BENCHMARK_UNAVAILABLE",
      severity: "warning",
      title: "Benchmark unavailable",
      message: `${benchmarkSymbol} adjusted-close history could not be aligned to the connected performance period.`,
    });
  }

  return {
    accountId,
    currency,
    source: "Webull and Portfolio Lab",
    quality: periods.length ? historicalQuality : "partial",
    asOf,
    dataThrough: historicalThrough,
    lastSuccessfulSyncAt: lastSyncedAt || asOf,
    performanceReadyFrom: start,
    holdingsReady: true,
    performanceReady: periods.length > 0 && timeWeightedReturn !== null,
    analyticsCoverage,
    metrics: {
      netAccountValue: currentMetric(balance.equity, "Net account value"),
      cashBalance: currentMetric(balance.cash, "Cash balance"),
      marketValue: currentMetric(balance.marketValue, "Market value"),
      dayProfitLoss: currentMetric(balance.dayProfitLoss, "Day profit and loss"),
      unrealizedProfitLoss: currentMetric(balance.unrealizedProfitLoss, "Unrealized profit and loss"),
      timeWeightedReturn: performanceMetric(timeWeightedReturn, "Time-weighted return", "percent"),
      benchmarkReturn: sourcedMetric(benchmarkReturn, `${benchmarkSymbol} return`, "Yahoo Finance adjusted close", benchmarkReturn === null ? "unavailable" : "verified", null, historicalThrough, "Adjusted-close total-return proxy aligned to common portfolio observation dates.", "percent", currency),
      excessReturn: performanceMetric(excessReturn, "Geometric excess return", "percent"),
      investmentGain: performanceMetric(investmentGain, "Investment gain", "currency"),
      netContributions: performanceMetric(netExternalFlow, "Net contributions", "currency"),
      moneyWeightedReturn: performanceMetric(moneyWeightedReturn, "Money-weighted return", "percent"),
      benchmarkSymbol,
      periodLabel: formatPeriodLabel(start, end),
    },
    chart: chart.points,
    holdings,
    activities,
    exclusions,
    issues,
  };
}

function buildPerformanceChart(
  periods: Record<string, unknown>[],
  benchmarkSeries: MarketSeriesPayload | null,
): { points: Record<string, unknown>[]; benchmarkReturn: number | null } {
  if (!periods.length) return { points: [], benchmarkReturn: null };
  const first = periods[0];
  const firstDate = dateOnly(first.start);
  const beginningValue = numberValue(first.beginningValue);
  if (!firstDate) return { points: [], benchmarkReturn: null };
  let growth: number | null = 100;
  const points: Record<string, unknown>[] = [{
    date: firstDate,
    portfolioGrowth: growth,
    portfolioValue: beginningValue,
    externalCashFlow: 0,
  }];
  for (const period of periods) {
    const date = dateOnly(period.end);
    if (!date) continue;
    const periodReturn = numberValue(period.modifiedDietzReturn);
    growth = growth !== null && periodReturn !== null ? growth * (1 + periodReturn) : null;
    points.push({
      date,
      portfolioGrowth: growth,
      portfolioValue: numberValue(period.endingValue),
      externalCashFlow: numberValue(period.netExternalFlow),
    });
  }

  const benchmarkByDate = new Map<string, number>();
  benchmarkSeries?.dates.forEach((date, index) => {
    const price = benchmarkSeries.prices[index];
    if (Number.isFinite(price) && price > 0) benchmarkByDate.set(date, price);
  });
  const common = points.flatMap((point) => {
    const date = typeof point.date === "string" ? point.date : "";
    const price = benchmarkByDate.get(date);
    return price === undefined ? [] : [{ point, price }];
  });
  const benchmarkBase = common[0]?.price ?? null;
  for (const point of points) {
    const date = typeof point.date === "string" ? point.date : "";
    const price = benchmarkByDate.get(date);
    point.benchmarkGrowth = benchmarkBase && price ? 100 * price / benchmarkBase : null;
  }
  const benchmarkReturn = benchmarkBase && common.length > 1
    ? common.at(-1)!.price / benchmarkBase - 1
    : null;
  return { points, benchmarkReturn };
}

function sourcedMetric(
  value: unknown,
  label: string,
  source: string,
  quality: string,
  asOf: string | null,
  dataThrough: string | null,
  methodology: string,
  unit: "currency" | "percent",
  currency: string,
): Record<string, unknown> {
  return { value: numberValue(value), label, source, quality, asOf, dataThrough, methodology, unit, currency };
}

function recordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.flatMap((item) => {
    const record = asRecord(item);
    return record ? [record] : [];
  }) : [];
}

function numberValue(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function dateValue(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

function dateOnly(value: unknown): string | null {
  return dateValue(value)?.slice(0, 10) || null;
}

function isEligiblePosition(position: Record<string, unknown>): boolean {
  const type = (nullableString(position.instrumentType) || "").toUpperCase();
  const currency = (nullableString(position.currency) || "USD").toUpperCase();
  const quantity = numberValue(position.quantity);
  const marketValue = numberValue(position.marketValue);
  return ["EQUITY", "STOCK", "ETF", "FUND"].includes(type)
    && currency === "USD"
    && quantity !== null
    && quantity > 0
    && marketValue !== null
    && marketValue > 0;
}

function exclusionReason(position: Record<string, unknown>): string {
  const type = (nullableString(position.instrumentType) || "UNKNOWN").toUpperCase();
  const currency = (nullableString(position.currency) || "USD").toUpperCase();
  const quantity = numberValue(position.quantity);
  if (currency !== "USD") return "Multi-currency performance is not supported in version one.";
  if (quantity !== null && quantity <= 0) return "Short and non-positive positions are excluded from the long-only engine.";
  return `${type} is retained in account value but excluded from long-only stock and ETF analytics.`;
}

function humanizeCode(value: string): string {
  const lower = value.toLowerCase().replace(/[_-]+/g, " ");
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

function formatPeriodLabel(start: string | null, end: string | null): string {
  if (!start || !end) return "Available period";
  const formatter = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" });
  return `${formatter.format(new Date(start))} to ${formatter.format(new Date(end))}`;
}

function redactSensitiveFields(value: unknown, depth = 0): unknown {
  if (depth > 8) return null;
  if (Array.isArray(value)) {
    return value.slice(0, 2_000).map((item) => redactSensitiveFields(item, depth + 1));
  }
  const record = asRecord(value);
  if (!record) return value;
  const result: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(record)) {
    if (/(?:secret|token|authorization|cookie|app[_-]?key|app[_-]?secret)/i.test(key)) {
      continue;
    }
    result[key] = redactSensitiveFields(item, depth + 1);
  }
  return result;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function nullableString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}
