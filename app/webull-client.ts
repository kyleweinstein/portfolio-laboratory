import type { HoldingInput } from "./analytics.ts";

export type WebullSource = "manual" | "webull";
export type WebullQuality = "verified" | "estimated" | "partial" | "stale" | "unavailable";
export type WebullNumber = number | string | null | undefined;

export type WebullAccount = {
  accountId: string;
  label: string;
  maskedIdentifier?: string | null;
  accountType?: string | null;
  currency?: string | null;
};

export type WebullProvenance = {
  source?: string | null;
  quality?: WebullQuality | string | null;
  asOf?: string | null;
  dataThrough?: string | null;
  methodology?: string | null;
  note?: string | null;
};

export type WebullMetric = WebullProvenance & {
  value: WebullNumber;
  label?: string | null;
  unit?: "currency" | "percent" | "number" | string | null;
  currency?: string | null;
};

export type WebullMetrics = {
  netAccountValue?: WebullMetric | null;
  cashBalance?: WebullMetric | null;
  marketValue?: WebullMetric | null;
  dayProfitLoss?: WebullMetric | null;
  timeWeightedReturn?: WebullMetric | null;
  benchmarkReturn?: WebullMetric | null;
  excessReturn?: WebullMetric | null;
  investmentGain?: WebullMetric | null;
  netContributions?: WebullMetric | null;
  moneyWeightedReturn?: WebullMetric | null;
  unrealizedProfitLoss?: WebullMetric | null;
  benchmarkSymbol?: string | null;
  periodLabel?: string | null;
};

export type WebullChartPoint = {
  date: string;
  portfolioGrowth?: WebullNumber;
  benchmarkGrowth?: WebullNumber;
  portfolioValue?: WebullNumber;
  externalCashFlow?: WebullNumber;
};

export type WebullHolding = WebullProvenance & {
  positionId?: string | null;
  symbol: string;
  name?: string | null;
  instrumentType?: string | null;
  quantity?: WebullNumber;
  marketValue?: WebullNumber;
  weight?: WebullNumber;
  currency?: string | null;
  costBasis?: WebullNumber;
  unrealizedProfitLoss?: WebullNumber;
  eligibleForAnalysis?: boolean | null;
  exclusionReason?: string | null;
};

export type WebullActivity = WebullProvenance & {
  activityId: string;
  date: string;
  type: string;
  symbol?: string | null;
  description?: string | null;
  amount?: WebullNumber;
  currency?: string | null;
  status?: string | null;
};

export type WebullExclusion = {
  symbol?: string | null;
  name?: string | null;
  instrumentType?: string | null;
  marketValue?: WebullNumber;
  currency?: string | null;
  reason: string;
};

export type WebullIssue = {
  issueId: string;
  severity: "info" | "warning" | "error" | string;
  title: string;
  message?: string | null;
  affectedMetric?: string | null;
  date?: string | null;
};

export type WebullDashboardData = WebullProvenance & {
  accountId: string;
  currency?: string | null;
  lastSuccessfulSyncAt?: string | null;
  performanceReadyFrom?: string | null;
  holdingsReady?: boolean;
  performanceReady?: boolean;
  analyticsCoverage?: WebullNumber;
  metrics?: WebullMetrics | null;
  chart?: WebullChartPoint[] | null;
  holdings?: WebullHolding[] | null;
  activities?: WebullActivity[] | null;
  exclusions?: WebullExclusion[] | null;
  issues?: WebullIssue[] | null;
};

export type WebullStatus = {
  enabled: boolean;
  authenticated: boolean;
  connected: boolean;
  verificationInProgress: boolean;
  csrfToken?: string | null;
  accounts: WebullAccount[];
  selectedAccountId: string | null;
  dashboard: WebullDashboardData | null;
};

export type WebullActionResult = {
  redirectUrl?: string | null;
  message?: string | null;
  status?: WebullStatus | null;
};

export type WebullClientOptions = {
  signal?: AbortSignal;
};

export class WebullApiError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(message: string, status: number, code: string | null = null) {
    super(message);
    this.name = "WebullApiError";
    this.status = status;
    this.code = code;
  }
}

type JsonRecord = Record<string, unknown>;
let portfolioCsrfToken: string | null = null;

function isRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function unwrapData(value: unknown): unknown {
  let current = value;
  for (let depth = 0; depth < 3; depth += 1) {
    if (!isRecord(current) || !("data" in current) || !isRecord(current.data)) break;
    current = current.data;
  }
  return current;
}

function textValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function booleanValue(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function accountFrom(value: unknown): WebullAccount | null {
  if (!isRecord(value)) return null;
  const accountId = textValue(value.accountId) || textValue(value.id);
  if (!accountId) return null;
  const last4 = textValue(value.last4);
  const maskedIdentifier = textValue(value.maskedIdentifier) || textValue(value.mask) || (last4 ? `••••${last4}` : null);
  return {
    accountId,
    label: textValue(value.label) || textValue(value.displayName) || textValue(value.name) || "Webull account",
    maskedIdentifier,
    accountType: textValue(value.accountType) || textValue(value.type),
    currency: textValue(value.currency),
  };
}

function dashboardFrom(value: unknown): WebullDashboardData | null {
  if (!isRecord(value)) return null;
  const accountId = textValue(value.accountId);
  if (!accountId) return null;
  return value as WebullDashboardData;
}

export function normalizeWebullStatus(payload: unknown): WebullStatus {
  const value = unwrapData(payload);
  if (!isRecord(value)) throw new WebullApiError("Webull returned an invalid status response.", 502, "INVALID_RESPONSE");

  const accounts = Array.isArray(value.accounts)
    ? value.accounts.map(accountFrom).filter((account): account is WebullAccount => Boolean(account))
    : [];
  const dashboard = dashboardFrom(value.dashboard);
  const selectedAccountId = textValue(value.selectedAccountId) || dashboard?.accountId || accounts[0]?.accountId || null;

  const csrfToken = textValue(value.csrfToken);
  if (csrfToken) portfolioCsrfToken = csrfToken;
  return {
    enabled: booleanValue(value.enabled, true),
    authenticated: booleanValue(value.authenticated, true),
    connected: booleanValue(value.connected, accounts.length > 0 || Boolean(dashboard)),
    verificationInProgress: booleanValue(value.verificationInProgress, false),
    csrfToken,
    accounts,
    selectedAccountId,
    dashboard,
  };
}

async function readPayload(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) return null;
  return response.json().catch(() => null);
}

function errorMessage(payload: unknown, fallback: string): { message: string; code: string | null } {
  const value = unwrapData(payload);
  if (!isRecord(value)) return { message: fallback, code: null };
  return {
    message: textValue(value.error) || textValue(value.message) || fallback,
    code: textValue(value.code),
  };
}

async function request(path: string, init: RequestInit = {}): Promise<unknown> {
  const method = (init.method || "GET").toUpperCase();
  const mutation = method !== "GET" && method !== "HEAD" && method !== "OPTIONS";
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: {
      accept: "application/json",
      ...(init.body ? { "content-type": "application/json" } : {}),
      ...(mutation && portfolioCsrfToken ? { "x-portfolio-csrf": portfolioCsrfToken } : {}),
      ...init.headers,
    },
  });
  const payload = await readPayload(response);
  if (!response.ok) {
    const fallback = response.status === 401 || response.status === 403
      ? "You are not authorized to access this connected portfolio."
      : `Webull request failed (${response.status}).`;
    const detail = errorMessage(payload, fallback);
    throw new WebullApiError(detail.message, response.status, detail.code);
  }
  return payload;
}

export function webullLoginUrl(returnTo = "/"): string {
  const safeReturnTo = returnTo.startsWith("/") && !returnTo.startsWith("//") ? returnTo : "/";
  return `/api/webull/auth/login?return_to=${safeReturnTo === "/" ? "/" : encodeURIComponent(safeReturnTo)}`;
}

function actionFrom(payload: unknown): WebullActionResult {
  const value = unwrapData(payload);
  if (!isRecord(value)) return {};
  const hasStatusShape = "enabled" in value || "connected" in value || "accounts" in value || "dashboard" in value;
  const nestedStatus = isRecord(value.status) ? normalizeWebullStatus(value.status) : null;
  const returnedCsrfToken = textValue(value.csrfToken);
  if (returnedCsrfToken) portfolioCsrfToken = returnedCsrfToken;
  return {
    redirectUrl: textValue(value.redirectUrl) || textValue(value.authorizationUrl),
    message: textValue(value.message),
    status: nestedStatus || (hasStatusShape ? normalizeWebullStatus(value) : null),
  };
}

export async function getWebullStatus(options: WebullClientOptions = {}): Promise<WebullStatus> {
  return normalizeWebullStatus(await request("/api/webull/status", { method: "GET", signal: options.signal }));
}

export async function connectWebull(options: WebullClientOptions = {}): Promise<WebullActionResult> {
  return actionFrom(await request("/api/webull/connect", { method: "POST", body: "{}", signal: options.signal }));
}

export async function selectWebullAccount(accountId: string, options: WebullClientOptions = {}): Promise<WebullActionResult> {
  return actionFrom(await request("/api/webull/accounts/select", {
    method: "POST",
    body: JSON.stringify({ accountId }),
    signal: options.signal,
  }));
}

export async function syncWebull(accountId: string | null, options: WebullClientOptions = {}): Promise<WebullActionResult> {
  return actionFrom(await request("/api/webull/sync", {
    method: "POST",
    body: JSON.stringify(accountId ? { accountId } : {}),
    signal: options.signal,
  }));
}

export async function backfillWebull(accountId: string | null, options: WebullClientOptions = {}): Promise<WebullActionResult> {
  return actionFrom(await request("/api/webull/backfill", {
    method: "POST",
    body: JSON.stringify(accountId ? { accountId } : {}),
    signal: options.signal,
  }));
}

export async function disconnectWebull(options: WebullClientOptions = {}): Promise<WebullActionResult> {
  return actionFrom(await request("/api/webull/connect", { method: "DELETE", signal: options.signal }));
}

export function finiteNumber(value: WebullNumber): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string" || !value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function isEquityType(value: string | null | undefined): boolean {
  if (!value) return false;
  const normalized = value.trim().toUpperCase();
  return normalized === "EQUITY" || normalized === "STOCK" || normalized === "ETF" || normalized === "FUND";
}

export function isWebullHoldingEligible(holding: WebullHolding): boolean {
  if (holding.eligibleForAnalysis === false) return false;
  if (holding.eligibleForAnalysis !== true && !isEquityType(holding.instrumentType)) return false;
  const basis = finiteNumber(holding.marketValue) ?? finiteNumber(holding.weight);
  return Boolean(holding.symbol.trim()) && basis !== null && basis > 0;
}

/** Build a normalized long-only equity sleeve for the existing analytics draft. */
export function buildEligibleWebullHoldings(holdings: readonly WebullHolding[]): HoldingInput[] {
  const values = new Map<string, number>();
  for (const holding of holdings) {
    const symbol = holding.symbol.trim().toUpperCase();
    if (!isWebullHoldingEligible(holding)) continue;
    const basis = finiteNumber(holding.marketValue) ?? finiteNumber(holding.weight);
    if (basis === null || basis <= 0) continue;
    values.set(symbol, (values.get(symbol) || 0) + basis);
  }

  const entries = [...values.entries()].sort(([left], [right]) => left.localeCompare(right));
  const total = entries.reduce((sum, [, value]) => sum + value, 0);
  if (!(total > 0)) return [];

  const result = entries.map(([symbol, value]) => ({ symbol, weight: Number(((value / total) * 100).toFixed(6)) }));
  const roundedTotal = result.reduce((sum, holding) => sum + holding.weight, 0);
  if (result.length) result[result.length - 1].weight = Number((result[result.length - 1].weight + (100 - roundedTotal)).toFixed(6));
  return result;
}
