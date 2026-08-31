import type { HoldingInput } from "./analytics.ts";

export type WebullSource = "manual" | "webull";
export type WebullQuality = "verified" | "estimated" | "partial" | "stale" | "unavailable";
export type WebullNumber = number | string | null | undefined;

export type WebullAccount = {
  accountRef: string;
  label: string;
  accountType?: string | null;
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
  unit?: "percent" | "number" | string | null;
};

export type WebullMetrics = {
  timeWeightedReturn?: WebullMetric | null;
  benchmarkReturn?: WebullMetric | null;
  excessReturn?: WebullMetric | null;
  moneyWeightedReturn?: WebullMetric | null;
  grossExposure?: WebullMetric | null;
  netExposure?: WebullMetric | null;
  cashMarginWeight?: WebullMetric | null;
  benchmarkSymbol?: string | null;
  periodLabel?: string | null;
};

export type WebullChartPoint = {
  date: string;
  portfolioReturn?: WebullNumber;
  benchmarkReturn?: WebullNumber;
};

export type WebullHolding = WebullProvenance & {
  kind?: "security" | "cash_margin" | "other" | string | null;
  symbol: string;
  name?: string | null;
  instrumentType?: string | null;
  weight?: WebullNumber;
  costBasisPerShare?: WebullNumber;
  returnPercent?: WebullNumber;
  eligibleForAnalysis?: boolean | null;
  exclusionReason?: string | null;
};

export type WebullExclusion = {
  symbol?: string | null;
  name?: string | null;
  instrumentType?: string | null;
  weight?: WebullNumber;
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
  lastSuccessfulSyncAt?: string | null;
  performanceReadyFrom?: string | null;
  holdingsReady?: boolean;
  performanceReady?: boolean;
  analyticsCoverage?: WebullNumber;
  metrics?: WebullMetrics | null;
  chart?: WebullChartPoint[] | null;
  holdings?: WebullHolding[] | null;
  exclusions?: WebullExclusion[] | null;
  issues?: WebullIssue[] | null;
};

export type WebullVerificationState = "running" | "succeeded" | "failed" | "timed_out";
export type WebullVerificationStage = "starting" | "verifying_access" | "discovering_accounts" | "syncing_account" | "finalizing" | "complete";
export type WebullNextAction = "sign_in" | "start_verification" | "wait" | "retry_verification" | "sync_account" | "view_portfolio" | "configure";

export type WebullVerification = {
  state: WebullVerificationState;
  stage: WebullVerificationStage;
  startedAt: string;
  updatedAt: string;
  completedAt: string | null;
  error: { code: string; message: string } | null;
};

export type WebullLastSyncAttempt = {
  status: "success" | "error";
  startedAt: string;
  completedAt: string;
  cashActivitiesComplete: boolean | null;
  message: string | null;
};

export type WebullStatus = {
  enabled: boolean;
  authenticated: boolean;
  connected: boolean;
  verificationInProgress: boolean;
  verification: WebullVerification | null;
  lastSyncAttempt: WebullLastSyncAttempt | null;
  nextAction: WebullNextAction;
  csrfToken?: string | null;
  accounts: WebullAccount[];
  selectedAccountRef: string | null;
  dashboard: WebullDashboardData | null;
  issues: WebullIssue[];
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

const VERIFICATION_STATES = new Set<WebullVerificationState>(["running", "succeeded", "failed", "timed_out"]);
const VERIFICATION_STAGES = new Set<WebullVerificationStage>(["starting", "verifying_access", "discovering_accounts", "syncing_account", "finalizing", "complete"]);
const NEXT_ACTIONS = new Set<WebullNextAction>(["sign_in", "start_verification", "wait", "retry_verification", "sync_account", "view_portfolio", "configure"]);

function timestampValue(value: unknown): string | null {
  const timestamp = textValue(value);
  if (!timestamp || !Number.isFinite(Date.parse(timestamp))) return null;
  return timestamp;
}

function verificationFrom(value: unknown): WebullVerification | null {
  if (!isRecord(value)) return null;
  const state = textValue(value.state);
  const stage = textValue(value.stage);
  const startedAt = timestampValue(value.startedAt);
  const updatedAt = timestampValue(value.updatedAt);
  if (!state || !VERIFICATION_STATES.has(state as WebullVerificationState) ||
      !stage || !VERIFICATION_STAGES.has(stage as WebullVerificationStage) ||
      !startedAt || !updatedAt) return null;

  const errorValue = isRecord(value.error) ? value.error : null;
  const errorCode = errorValue ? textValue(errorValue.code) : null;
  const errorMessage = errorValue ? textValue(errorValue.message) : null;
  return {
    state: state as WebullVerificationState,
    stage: stage as WebullVerificationStage,
    startedAt,
    updatedAt,
    completedAt: timestampValue(value.completedAt),
    error: errorCode && errorMessage ? { code: errorCode.slice(0, 80), message: errorMessage.slice(0, 300) } : null,
  };
}

function lastSyncAttemptFrom(value: unknown): WebullLastSyncAttempt | null {
  if (!isRecord(value)) return null;
  const status = textValue(value.status);
  const startedAt = timestampValue(value.startedAt);
  const completedAt = timestampValue(value.completedAt);
  const cashActivitiesComplete = value.cashActivitiesComplete;
  if ((status !== "success" && status !== "error") || !startedAt || !completedAt ||
      (cashActivitiesComplete !== null && cashActivitiesComplete !== undefined && typeof cashActivitiesComplete !== "boolean")) {
    return null;
  }
  return {
    status,
    startedAt,
    completedAt,
    cashActivitiesComplete: typeof cashActivitiesComplete === "boolean" ? cashActivitiesComplete : null,
    message: textValue(value.message)?.slice(0, 300) || null,
  };
}

function issueFrom(value: unknown): WebullIssue | null {
  if (!isRecord(value)) return null;
  const issueId = textValue(value.issueId);
  const title = textValue(value.title);
  if (!issueId || !title) return null;
  return {
    issueId: issueId.slice(0, 100),
    severity: textValue(value.severity)?.slice(0, 24) || "info",
    title: title.slice(0, 160),
    message: textValue(value.message)?.slice(0, 500) || null,
    affectedMetric: textValue(value.affectedMetric)?.slice(0, 100) || null,
    date: timestampValue(value.date) || null,
  };
}

function fallbackNextAction(enabled: boolean, authenticated: boolean, connected: boolean, dashboard: WebullDashboardData | null, verification: WebullVerification | null): WebullNextAction {
  if (!enabled) return "configure";
  if (!authenticated) return "sign_in";
  if (verification?.state === "running") return "wait";
  if (connected) return dashboard ? "view_portfolio" : "sync_account";
  if (verification?.state === "failed" || verification?.state === "timed_out") return "retry_verification";
  return "start_verification";
}

function accountFrom(value: unknown): WebullAccount | null {
  if (!isRecord(value)) return null;
  const accountRef = textValue(value.accountRef);
  if (!accountRef || !/^wbr_[A-Za-z0-9_-]{24,64}$/.test(accountRef)) return null;
  return {
    accountRef,
    label: textValue(value.label) || textValue(value.displayName) || textValue(value.name) || "Webull account",
    accountType: textValue(value.accountType) || textValue(value.type),
  };
}

function dashboardFrom(value: unknown): WebullDashboardData | null {
  if (!isRecord(value)) return null;
  return value as WebullDashboardData;
}

export function normalizeWebullStatus(payload: unknown): WebullStatus {
  const value = unwrapData(payload);
  if (!isRecord(value)) throw new WebullApiError("Webull returned an invalid status response.", 502, "INVALID_RESPONSE");

  const accounts = Array.isArray(value.accounts)
    ? value.accounts.map(accountFrom).filter((account): account is WebullAccount => Boolean(account))
    : [];
  const dashboard = dashboardFrom(value.dashboard);
  const selectedAccountRef = textValue(value.selectedAccountRef) || accounts[0]?.accountRef || null;
  const enabled = booleanValue(value.enabled, true);
  const authenticated = booleanValue(value.authenticated, true);
  const connected = booleanValue(value.connected, accounts.length > 0 || Boolean(dashboard));
  const verification = authenticated ? verificationFrom(value.verification) : null;
  const lastSyncAttempt = authenticated ? lastSyncAttemptFrom(value.lastSyncAttempt) : null;
  const issues = authenticated && Array.isArray(value.issues)
    ? value.issues.map(issueFrom).filter((issue): issue is WebullIssue => Boolean(issue))
    : [];
  const candidateNextAction = textValue(value.nextAction);
  const nextAction = !enabled
    ? "configure"
    : !authenticated
      ? "sign_in"
      : candidateNextAction && NEXT_ACTIONS.has(candidateNextAction as WebullNextAction)
        ? candidateNextAction as WebullNextAction
        : fallbackNextAction(enabled, authenticated, connected, dashboard, verification);

  const csrfToken = textValue(value.csrfToken);
  if (csrfToken) portfolioCsrfToken = csrfToken;
  return {
    enabled,
    authenticated,
    connected,
    verificationInProgress: verification
      ? verification.state === "running"
      : booleanValue(value.verificationInProgress, false),
    verification,
    lastSyncAttempt,
    nextAction,
    csrfToken,
    accounts,
    selectedAccountRef,
    dashboard,
    issues,
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
    const fallback = response.status === 401
      ? "Your Portfolio Lab owner session has expired."
      : response.status === 403
        ? "Portfolio Lab rejected this protected request. Refresh the status before retrying."
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
  const hasStatusShape = "enabled" in value || "connected" in value || "accounts" in value || "dashboard" in value || "verification" in value || "lastSyncAttempt" in value || "nextAction" in value;
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

export async function selectWebullAccount(accountRef: string, options: WebullClientOptions = {}): Promise<WebullActionResult> {
  return actionFrom(await request("/api/webull/accounts/select", {
    method: "POST",
    body: JSON.stringify({ accountRef }),
    signal: options.signal,
  }));
}

export async function syncWebull(accountRef: string | null, options: WebullClientOptions = {}): Promise<WebullActionResult> {
  return actionFrom(await request("/api/webull/sync", {
    method: "POST",
    body: JSON.stringify(accountRef ? { accountRef } : {}),
    signal: options.signal,
  }));
}

export async function backfillWebull(accountRef: string | null, options: WebullClientOptions = {}): Promise<WebullActionResult> {
  return actionFrom(await request("/api/webull/backfill", {
    method: "POST",
    body: JSON.stringify(accountRef ? { accountRef } : {}),
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
  const basis = finiteNumber(holding.weight);
  return Boolean(holding.symbol.trim()) && basis !== null && basis > 0;
}

/** Build a normalized long-only equity sleeve for the existing analytics draft. */
export function buildEligibleWebullHoldings(holdings: readonly WebullHolding[]): HoldingInput[] {
  const values = new Map<string, number>();
  for (const holding of holdings) {
    const symbol = holding.symbol.trim().toUpperCase();
    if (!isWebullHoldingEligible(holding)) continue;
    const basis = finiteNumber(holding.weight);
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
