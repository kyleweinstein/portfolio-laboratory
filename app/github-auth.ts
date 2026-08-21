const encoder = new TextEncoder();

export const GITHUB_OAUTH_STATE_COOKIE =
  "__Host-portfolio_lab_github_oauth_state";
export const GITHUB_SESSION_COOKIE =
  "__Host-portfolio_lab_github_session";
export const GITHUB_CSRF_HEADER = "x-portfolio-csrf";

const OAUTH_STATE_TTL_SECONDS = 10 * 60;
const DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60;
const MIN_SESSION_TTL_SECONDS = 15 * 60;
const MAX_SESSION_TTL_SECONDS = 24 * 60 * 60;
const GITHUB_API_VERSION = "2022-11-28";

export type Environment = Record<string, string | undefined>;

export type GitHubAuthConfig = {
  clientId: string;
  clientSecret: string;
  sessionSecret: string;
  ownerIds: ReadonlySet<string>;
  sessionTtlSeconds: number;
};

export type GitHubSession = {
  githubId: string;
  login: string;
  csrfToken: string;
  expiresAt: number;
};

export type GitHubIdentity = {
  id: string;
  login: string;
};

type OAuthStatePayload = {
  v: 1;
  state: string;
  returnTo: string;
  exp: number;
};

type SessionPayload = {
  v: 1;
  githubId: string;
  login: string;
  csrf: string;
  exp: number;
};

export class GitHubAuthConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "GitHubAuthConfigurationError";
  }
}

function runtimeEnvironment(): Environment {
  return typeof process === "undefined" ? {} : process.env;
}

export function parseGitHubOwnerIds(value: string | undefined): Set<string> {
  const result = new Set<string>();
  for (const candidate of (value ?? "").split(",")) {
    const id = candidate.trim();
    if (!id) continue;
    if (!/^[1-9]\d*$/.test(id)) {
      throw new GitHubAuthConfigurationError(
        "GITHUB_OWNER_IDS must contain only positive numeric GitHub user IDs.",
      );
    }
    result.add(id);
  }
  return result;
}

export function getGitHubAuthConfig(
  env: Environment = runtimeEnvironment(),
): GitHubAuthConfig {
  const clientId = env.GITHUB_CLIENT_ID?.trim() ?? "";
  const clientSecret = env.GITHUB_CLIENT_SECRET?.trim() ?? "";
  const sessionSecret = env.GITHUB_SESSION_SECRET ?? "";
  const ownerIds = parseGitHubOwnerIds(
    [env.GITHUB_OWNER_ID, env.GITHUB_OWNER_IDS].filter(Boolean).join(","),
  );

  if (!clientId || !clientSecret) {
    throw new GitHubAuthConfigurationError(
      "GitHub OAuth is not configured.",
    );
  }
  if (encoder.encode(sessionSecret).byteLength < 32) {
    throw new GitHubAuthConfigurationError(
      "GITHUB_SESSION_SECRET must be at least 32 bytes.",
    );
  }
  if (!ownerIds.size) {
    throw new GitHubAuthConfigurationError(
      "At least one stable numeric GitHub owner ID is required.",
    );
  }

  return {
    clientId,
    clientSecret,
    sessionSecret,
    ownerIds,
    sessionTtlSeconds: boundedInteger(
      env.GITHUB_SESSION_TTL_SECONDS,
      DEFAULT_SESSION_TTL_SECONDS,
      MIN_SESSION_TTL_SECONDS,
      MAX_SESSION_TTL_SECONDS,
    ),
  };
}

function getSessionVerificationConfig(
  env: Environment = runtimeEnvironment(),
): Pick<GitHubAuthConfig, "sessionSecret" | "ownerIds"> | null {
  const sessionSecret = env.GITHUB_SESSION_SECRET ?? "";
  let ownerIds: Set<string>;
  try {
    ownerIds = parseGitHubOwnerIds(
      [env.GITHUB_OWNER_ID, env.GITHUB_OWNER_IDS].filter(Boolean).join(","),
    );
  } catch {
    return null;
  }
  if (encoder.encode(sessionSecret).byteLength < 32 || !ownerIds.size) {
    return null;
  }
  return { sessionSecret, ownerIds };
}

export function safeReturnTo(value: string | null | undefined): string {
  if (!value?.startsWith("/") || value.startsWith("//")) return "/";
  try {
    const parsed = new URL(value, "https://portfolio.local");
    if (parsed.origin !== "https://portfolio.local") return "/";
    if (parsed.pathname.startsWith("/api/webull/auth/")) return "/";
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return "/";
  }
}

export function resolveGitHubCallbackUrl(
  request: Request,
  env: Environment = runtimeEnvironment(),
): string {
  const configured = env.GITHUB_OAUTH_REDIRECT_URI?.trim();
  const callback = configured
    ? new URL(configured)
    : new URL("/api/webull/auth/callback", request.url);
  if (
    callback.protocol !== "https:" &&
    !(callback.protocol === "http:" && isLoopbackHost(callback.hostname))
  ) {
    throw new GitHubAuthConfigurationError(
      "The GitHub OAuth callback must use HTTPS.",
    );
  }
  callback.username = "";
  callback.password = "";
  callback.hash = "";
  return callback.toString();
}

export async function createOAuthState(
  returnTo: string,
  secret: string,
  now = Date.now(),
): Promise<{ state: string; cookieValue: string }> {
  const payload: OAuthStatePayload = {
    v: 1,
    state: randomToken(32),
    returnTo: safeReturnTo(returnTo),
    exp: Math.floor(now / 1000) + OAUTH_STATE_TTL_SECONDS,
  };
  return {
    state: payload.state,
    cookieValue: await signPayload(payload, "github-oauth-state", secret),
  };
}

export async function readOAuthState(
  request: Request,
  secret: string,
  now = Date.now(),
): Promise<OAuthStatePayload | null> {
  const value = readCookie(request, GITHUB_OAUTH_STATE_COOKIE);
  if (!value) return null;
  const payload = await verifyPayload<OAuthStatePayload>(
    value,
    "github-oauth-state",
    secret,
  );
  if (!isOAuthStatePayload(payload)) return null;
  if (payload.exp < Math.floor(now / 1000)) return null;
  return payload;
}

export function buildGitHubAuthorizationUrl(
  config: GitHubAuthConfig,
  callbackUrl: string,
  state: string,
): string {
  const url = new URL("https://github.com/login/oauth/authorize");
  url.searchParams.set("client_id", config.clientId);
  url.searchParams.set("redirect_uri", callbackUrl);
  url.searchParams.set("scope", "read:user");
  url.searchParams.set("state", state);
  url.searchParams.set("allow_signup", "false");
  return url.toString();
}

export async function exchangeGitHubCode(
  code: string,
  callbackUrl: string,
  config: GitHubAuthConfig,
): Promise<string> {
  const response = await fetch("https://github.com/login/oauth/access_token", {
    method: "POST",
    headers: {
      accept: "application/json",
      "content-type": "application/x-www-form-urlencoded",
      "user-agent": "portfolio-lab-webull-auth",
    },
    body: new URLSearchParams({
      client_id: config.clientId,
      client_secret: config.clientSecret,
      code,
      redirect_uri: callbackUrl,
    }),
    redirect: "error",
    signal: AbortSignal.timeout(10_000),
  });
  const body = await readJsonObject(response);
  const accessToken = stringField(body, "access_token", 4_096);
  if (!response.ok || !accessToken) {
    throw new Error("GitHub rejected the OAuth authorization code.");
  }
  return accessToken;
}

export async function fetchGitHubIdentity(
  accessToken: string,
): Promise<GitHubIdentity> {
  const response = await fetch("https://api.github.com/user", {
    headers: {
      accept: "application/vnd.github+json",
      authorization: `Bearer ${accessToken}`,
      "user-agent": "portfolio-lab-webull-auth",
      "x-github-api-version": GITHUB_API_VERSION,
    },
    redirect: "error",
    signal: AbortSignal.timeout(10_000),
  });
  const body = await readJsonObject(response);
  const id = numericIdentity(body.id);
  const login = stringField(body, "login", 100);
  if (!response.ok || !id || !login) {
    throw new Error("GitHub did not return a valid user identity.");
  }
  return { id, login };
}

export async function createGitHubSession(
  identity: GitHubIdentity,
  config: GitHubAuthConfig,
  now = Date.now(),
): Promise<{ session: GitHubSession; cookieValue: string }> {
  if (!config.ownerIds.has(identity.id)) {
    throw new Error("This GitHub account is not authorized.");
  }
  const payload: SessionPayload = {
    v: 1,
    githubId: identity.id,
    login: identity.login,
    csrf: randomToken(32),
    exp: Math.floor(now / 1000) + config.sessionTtlSeconds,
  };
  return {
    session: sessionFromPayload(payload),
    cookieValue: await signPayload(
      payload,
      "github-owner-session",
      config.sessionSecret,
    ),
  };
}

export async function readGitHubSession(
  request: Request,
  env: Environment = runtimeEnvironment(),
  now = Date.now(),
): Promise<GitHubSession | null> {
  const config = getSessionVerificationConfig(env);
  if (!config) return null;
  const value = readCookie(request, GITHUB_SESSION_COOKIE);
  if (!value) return null;
  const payload = await verifyPayload<SessionPayload>(
    value,
    "github-owner-session",
    config.sessionSecret,
  );
  if (!isSessionPayload(payload)) return null;
  if (payload.exp < Math.floor(now / 1000)) return null;
  if (!config.ownerIds.has(payload.githubId)) return null;
  return sessionFromPayload(payload);
}

export function validateMutationRequest(
  request: Request,
  session: GitHubSession,
): { ok: true } | { ok: false; error: string } {
  const fetchSite = request.headers.get("sec-fetch-site");
  if (fetchSite && fetchSite !== "same-origin" && fetchSite !== "none") {
    return { ok: false, error: "Cross-origin requests are not allowed." };
  }

  const expectedOrigin = new URL(request.url).origin;
  const origin = request.headers.get("origin");
  const referer = request.headers.get("referer");
  let suppliedOrigin: string | null = null;
  try {
    suppliedOrigin = origin
      ? new URL(origin).origin
      : referer
        ? new URL(referer).origin
        : null;
  } catch {
    return { ok: false, error: "The request origin is invalid." };
  }
  if (suppliedOrigin !== expectedOrigin) {
    return { ok: false, error: "A same-origin request is required." };
  }

  const csrfToken = request.headers.get(GITHUB_CSRF_HEADER) ?? "";
  if (!constantTimeStringEqual(csrfToken, session.csrfToken)) {
    return { ok: false, error: "The CSRF token is missing or invalid." };
  }
  return { ok: true };
}

export function serializeSecureCookie(
  name: string,
  value: string,
  maxAgeSeconds: number,
): string {
  return [
    `${name}=${encodeURIComponent(value)}`,
    "Path=/",
    `Max-Age=${Math.max(0, Math.floor(maxAgeSeconds))}`,
    "HttpOnly",
    "Secure",
    "SameSite=Lax",
  ].join("; ");
}

export function expireSecureCookie(name: string): string {
  return serializeSecureCookie(name, "", 0);
}

export function oauthStateCookie(value: string): string {
  return serializeSecureCookie(
    GITHUB_OAUTH_STATE_COOKIE,
    value,
    OAUTH_STATE_TTL_SECONDS,
  );
}

export function sessionCookie(
  value: string,
  config: GitHubAuthConfig,
): string {
  return serializeSecureCookie(
    GITHUB_SESSION_COOKIE,
    value,
    config.sessionTtlSeconds,
  );
}

export async function signPayload(
  payload: unknown,
  purpose: string,
  secret: string,
): Promise<string> {
  const encodedPayload = base64UrlEncode(
    encoder.encode(JSON.stringify(payload)),
  );
  const signature = await hmac(
    `${purpose}.${encodedPayload}`,
    secret,
  );
  return `${encodedPayload}.${base64UrlEncode(signature)}`;
}

export async function verifyPayload<T>(
  value: string,
  purpose: string,
  secret: string,
): Promise<T | null> {
  const parts = value.split(".");
  if (parts.length !== 2 || parts.some((part) => !part)) return null;
  const [encodedPayload, encodedSignature] = parts;
  const supplied = base64UrlDecode(encodedSignature);
  const payloadBytes = base64UrlDecode(encodedPayload);
  if (!supplied || !payloadBytes) return null;
  const expected = await hmac(`${purpose}.${encodedPayload}`, secret);
  if (!constantTimeBytesEqual(supplied, expected)) return null;
  try {
    return JSON.parse(new TextDecoder().decode(payloadBytes)) as T;
  } catch {
    return null;
  }
}

function readCookie(request: Request, name: string): string | null {
  for (const item of (request.headers.get("cookie") ?? "").split(";")) {
    const separator = item.indexOf("=");
    if (separator < 0 || item.slice(0, separator).trim() !== name) continue;
    try {
      return decodeURIComponent(item.slice(separator + 1).trim());
    } catch {
      return null;
    }
  }
  return null;
}

function sessionFromPayload(payload: SessionPayload): GitHubSession {
  return {
    githubId: payload.githubId,
    login: payload.login,
    csrfToken: payload.csrf,
    expiresAt: payload.exp,
  };
}

function isOAuthStatePayload(value: unknown): value is OAuthStatePayload {
  if (!isRecord(value)) return false;
  return (
    value.v === 1 &&
    typeof value.state === "string" &&
    /^[A-Za-z0-9_-]{32,}$/.test(value.state) &&
    typeof value.returnTo === "string" &&
    safeReturnTo(value.returnTo) === value.returnTo &&
    typeof value.exp === "number" &&
    Number.isSafeInteger(value.exp)
  );
}

function isSessionPayload(value: unknown): value is SessionPayload {
  if (!isRecord(value)) return false;
  return (
    value.v === 1 &&
    typeof value.githubId === "string" &&
    /^[1-9]\d*$/.test(value.githubId) &&
    typeof value.login === "string" &&
    value.login.length > 0 &&
    value.login.length <= 100 &&
    typeof value.csrf === "string" &&
    /^[A-Za-z0-9_-]{32,}$/.test(value.csrf) &&
    typeof value.exp === "number" &&
    Number.isSafeInteger(value.exp)
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringField(
  value: Record<string, unknown>,
  key: string,
  maxLength: number,
): string | null {
  const field = value[key];
  return typeof field === "string" && field.length > 0 && field.length <= maxLength
    ? field
    : null;
}

function numericIdentity(value: unknown): string | null {
  if (typeof value === "number") {
    return Number.isSafeInteger(value) && value > 0 ? String(value) : null;
  }
  if (typeof value === "string" && /^[1-9]\d*$/.test(value)) return value;
  return null;
}

async function readJsonObject(response: Response): Promise<Record<string, unknown>> {
  const text = await response.text();
  if (text.length > 1_000_000) return {};
  try {
    const value: unknown = JSON.parse(text);
    return isRecord(value) ? value : {};
  } catch {
    return {};
  }
}

async function hmac(value: string, secret: string): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return new Uint8Array(
    await crypto.subtle.sign("HMAC", key, encoder.encode(value)),
  );
}

function base64UrlEncode(bytes: Uint8Array): string {
  let binary = "";
  for (let index = 0; index < bytes.length; index += 1) {
    binary += String.fromCharCode(bytes[index]);
  }
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function base64UrlDecode(value: string): Uint8Array | null {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) return null;
  try {
    const padded = value.replace(/-/g, "+").replace(/_/g, "/") +
      "=".repeat((4 - (value.length % 4)) % 4);
    const binary = atob(padded);
    return Uint8Array.from(binary, (character) => character.charCodeAt(0));
  } catch {
    return null;
  }
}

function constantTimeBytesEqual(left: Uint8Array, right: Uint8Array): boolean {
  let difference = left.length ^ right.length;
  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    difference |= (left[index] ?? 0) ^ (right[index] ?? 0);
  }
  return difference === 0;
}

function constantTimeStringEqual(left: string, right: string): boolean {
  return constantTimeBytesEqual(encoder.encode(left), encoder.encode(right));
}

function randomToken(byteLength: number): string {
  const bytes = new Uint8Array(byteLength);
  crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

function boundedInteger(
  value: string | undefined,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= minimum && parsed <= maximum
    ? parsed
    : fallback;
}

function isLoopbackHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
}
