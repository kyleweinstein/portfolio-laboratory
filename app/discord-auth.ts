import {
  expireSecureCookie,
  serializeSecureCookie,
  signPayload,
  verifyPayload,
  type Environment,
} from "./github-auth.ts";
import {
  createDiscordSessionStore,
  type DiscordPrivateSession,
  type DiscordSessionStore,
} from "./discord-session-store.ts";

const encoder = new TextEncoder();

export const DISCORD_OAUTH_STATE_COOKIE =
  "__Host-portfolio_lab_discord_oauth_state";
export const DISCORD_SESSION_COOKIE =
  "__Host-portfolio_lab_discord_session";

const DISCORD_API_BASE = "https://discord.com/api/v10";
const DISCORD_AUTHORIZE_URL = "https://discord.com/oauth2/authorize";
const DISCORD_TOKEN_URL = `${DISCORD_API_BASE}/oauth2/token`;
const OAUTH_STATE_TTL_SECONDS = 10 * 60;
const SESSION_TTL_SECONDS = 12 * 60 * 60;
export const DISCORD_MEMBERSHIP_RECHECK_SECONDS = 5 * 60;
const TOKEN_REFRESH_MARGIN_SECONDS = 60;

type DiscordOAuthState = {
  v: 1;
  state: string;
  returnTo: string;
  exp: number;
};

type DiscordSessionPayload = DiscordPrivateSession;

export type DiscordViewer = {
  discordId: string;
  username: string;
  displayName: string;
  membershipCheckedAt: number;
  expiresAt: number;
};

export type DiscordAuthConfig = {
  clientId: string;
  clientSecret: string;
  guildId: string;
  sessionSecret: string;
  sessionTtlSeconds: number;
};

export type DiscordSessionInspection =
  | { state: "authenticated"; viewer: DiscordViewer }
  | { state: "stale"; viewer: DiscordViewer }
  | { state: "unavailable" }
  | { state: "unauthenticated" };

export type DiscordRevalidation =
  | {
      ok: true;
      viewer: DiscordViewer;
      cookieValue: string | null;
      cookieMaxAgeSeconds: number;
    }
  | {
      ok: false;
      status: 401 | 403 | 503;
      reason: "unauthenticated" | "not_a_member" | "verification_unavailable";
      clearCookie: boolean;
    };

export class DiscordAuthConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DiscordAuthConfigurationError";
  }
}

export class DiscordMembershipError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "DiscordMembershipError";
    this.status = status;
  }
}

function runtimeEnvironment(): Environment {
  return typeof process === "undefined" ? {} : process.env;
}

export function getDiscordAuthConfig(
  env: Environment = runtimeEnvironment(),
): DiscordAuthConfig {
  const clientId = env.DISCORD_CLIENT_ID?.trim() ?? "";
  const clientSecret = env.DISCORD_CLIENT_SECRET?.trim() ?? "";
  const guildId = env.DISCORD_GUILD_ID?.trim() ?? "";
  const sessionSecret = env.DISCORD_SESSION_SECRET ?? "";

  if (!/^\d+$/.test(clientId) || !clientSecret) {
    throw new DiscordAuthConfigurationError("Discord OAuth is not configured.");
  }
  if (!/^[1-9]\d*$/.test(guildId)) {
    throw new DiscordAuthConfigurationError(
      "DISCORD_GUILD_ID must be a numeric Discord server ID.",
    );
  }
  if (encoder.encode(sessionSecret).byteLength < 32) {
    throw new DiscordAuthConfigurationError(
      "DISCORD_SESSION_SECRET must be at least 32 bytes.",
    );
  }

  return {
    clientId,
    clientSecret,
    guildId,
    sessionSecret,
    sessionTtlSeconds: boundedInteger(
      env.DISCORD_SESSION_TTL_SECONDS,
      SESSION_TTL_SECONDS,
      15 * 60,
      7 * 24 * 60 * 60,
    ),
  };
}

export function safeDiscordReturnTo(value: string | null | undefined): string {
  if (!value?.startsWith("/") || value.startsWith("//")) return "/portfolios";
  try {
    const url = new URL(value, "https://portfolio.local");
    if (url.origin !== "https://portfolio.local") return "/portfolios";
    if (url.pathname.startsWith("/api/discord/auth/")) return "/portfolios";
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return "/portfolios";
  }
}

export function resolveDiscordCallbackUrl(
  request: Request,
  env: Environment = runtimeEnvironment(),
): string {
  const configured = env.DISCORD_OAUTH_REDIRECT_URI?.trim();
  const callback = configured
    ? new URL(configured)
    : new URL("/api/discord/auth/callback", request.url);
  if (
    callback.protocol !== "https:" &&
    !(callback.protocol === "http:" && isLoopbackHost(callback.hostname))
  ) {
    throw new DiscordAuthConfigurationError(
      "The Discord OAuth callback must use HTTPS.",
    );
  }
  callback.username = "";
  callback.password = "";
  callback.hash = "";
  return callback.toString();
}

export async function createDiscordOAuthState(
  returnTo: string,
  secret: string,
  now = Date.now(),
): Promise<{ state: string; cookieValue: string }> {
  const payload: DiscordOAuthState = {
    v: 1,
    state: randomToken(32),
    returnTo: safeDiscordReturnTo(returnTo),
    exp: Math.floor(now / 1000) + OAUTH_STATE_TTL_SECONDS,
  };
  return {
    state: payload.state,
    cookieValue: await signPayload(payload, "discord-oauth-state", secret),
  };
}

export async function readDiscordOAuthState(
  request: Request,
  secret: string,
  now = Date.now(),
): Promise<DiscordOAuthState | null> {
  const value = readCookie(request, DISCORD_OAUTH_STATE_COOKIE);
  if (!value) return null;
  const payload = await verifyPayload<DiscordOAuthState>(
    value,
    "discord-oauth-state",
    secret,
  );
  if (!isDiscordOAuthState(payload)) return null;
  if (payload.exp < Math.floor(now / 1000)) return null;
  return payload;
}

export function buildDiscordAuthorizationUrl(
  config: DiscordAuthConfig,
  callbackUrl: string,
  state: string,
): string {
  const url = new URL(DISCORD_AUTHORIZE_URL);
  url.searchParams.set("client_id", config.clientId);
  url.searchParams.set("redirect_uri", callbackUrl);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", "identify guilds.members.read");
  url.searchParams.set("state", state);
  url.searchParams.set("prompt", "consent");
  return url.toString();
}

export async function exchangeDiscordCode(
  code: string,
  callbackUrl: string,
  config: DiscordAuthConfig,
): Promise<DiscordToken> {
  return requestDiscordToken(
    new URLSearchParams({
      client_id: config.clientId,
      client_secret: config.clientSecret,
      grant_type: "authorization_code",
      code,
      redirect_uri: callbackUrl,
    }),
  );
}

async function refreshDiscordAccessToken(
  refreshToken: string,
  config: DiscordAuthConfig,
): Promise<DiscordToken> {
  return requestDiscordToken(
    new URLSearchParams({
      client_id: config.clientId,
      client_secret: config.clientSecret,
      grant_type: "refresh_token",
      refresh_token: refreshToken,
    }),
  );
}

type DiscordToken = {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
};

async function requestDiscordToken(body: URLSearchParams): Promise<DiscordToken> {
  const response = await fetch(DISCORD_TOKEN_URL, {
    method: "POST",
    headers: {
      accept: "application/json",
      "content-type": "application/x-www-form-urlencoded",
    },
    body,
    redirect: "error",
    signal: AbortSignal.timeout(10_000),
  });
  const value = await readJsonObject(response);
  const accessToken = stringField(value, "access_token", 4_096);
  const refreshToken = stringField(value, "refresh_token", 4_096);
  const expiresIn = finiteInteger(value.expires_in);
  if (!response.ok || !accessToken || !refreshToken || !expiresIn) {
    throw new Error("Discord rejected the OAuth authorization.");
  }
  return { accessToken, refreshToken, expiresIn };
}

type DiscordGuildMember = {
  discordId: string;
  username: string;
  displayName: string;
};

export async function fetchDiscordGuildMember(
  accessToken: string,
  guildId: string,
): Promise<DiscordGuildMember> {
  const response = await fetch(
    `${DISCORD_API_BASE}/users/@me/guilds/${encodeURIComponent(guildId)}/member`,
    {
      headers: {
        accept: "application/json",
        authorization: `Bearer ${accessToken}`,
      },
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
    },
  );
  const value = await readJsonObject(response);
  if (response.status === 401 || response.status === 403 || response.status === 404) {
    throw new DiscordMembershipError("Discord server membership is required.", response.status);
  }
  if (!response.ok) {
    throw new DiscordMembershipError("Discord membership could not be verified.", response.status);
  }
  if (value.pending === true) {
    throw new DiscordMembershipError(
      "Complete the Discord server membership screening before continuing.",
      403,
    );
  }
  const user = asRecord(value.user);
  const discordId = user ? stringField(user, "id", 32) : null;
  const username = user ? stringField(user, "username", 100) : null;
  const globalName = user ? nullableStringField(user, "global_name", 100) : null;
  const nickname = nullableStringField(value, "nick", 100);
  if (!discordId || !/^[1-9]\d*$/.test(discordId) || !username) {
    throw new DiscordMembershipError("Discord returned an invalid member record.", 502);
  }
  return {
    discordId,
    username,
    displayName: nickname ?? globalName ?? username,
  };
}

export async function createDiscordSession(
  member: DiscordGuildMember,
  token: DiscordToken,
  config: DiscordAuthConfig,
  now = Date.now(),
  store: DiscordSessionStore = createDiscordSessionStore(),
): Promise<{ viewer: DiscordViewer; cookieValue: string }> {
  const nowSeconds = Math.floor(now / 1000);
  const payload: DiscordSessionPayload = {
    v: 1,
    ...member,
    guildId: config.guildId,
    accessToken: token.accessToken,
    refreshToken: token.refreshToken,
    tokenExpiresAt: nowSeconds + token.expiresIn,
    membershipCheckedAt: nowSeconds,
    exp: nowSeconds + config.sessionTtlSeconds,
  };
  const sessionId = randomToken(32);
  await store.write(sessionId, payload);
  return {
    viewer: viewerFrom(payload),
    cookieValue: sessionId,
  };
}

export async function inspectDiscordSession(
  request: Request,
  env: Environment = runtimeEnvironment(),
  now = Date.now(),
  store?: DiscordSessionStore,
): Promise<DiscordSessionInspection> {
  let config: DiscordAuthConfig;
  try {
    config = getDiscordAuthConfig(env);
  } catch {
    return { state: "unauthenticated" };
  }
  let payload: DiscordSessionPayload | null;
  try {
    payload = await readDiscordSessionPayload(
      request,
      config,
      store ?? createDiscordSessionStore(env),
    );
  } catch {
    return { state: "unavailable" };
  }
  const nowSeconds = Math.floor(now / 1000);
  if (!payload || payload.exp <= nowSeconds) return { state: "unauthenticated" };
  const viewer = viewerFrom(payload);
  return nowSeconds - payload.membershipCheckedAt < DISCORD_MEMBERSHIP_RECHECK_SECONDS
    ? { state: "authenticated", viewer }
    : { state: "stale", viewer };
}

export async function revalidateDiscordSession(
  request: Request,
  env: Environment = runtimeEnvironment(),
  now = Date.now(),
  store?: DiscordSessionStore,
): Promise<DiscordRevalidation> {
  let config: DiscordAuthConfig;
  try {
    config = getDiscordAuthConfig(env);
  } catch {
    return {
      ok: false,
      status: 503,
      reason: "verification_unavailable",
      clearCookie: false,
    };
  }
  let runtimeStore: DiscordSessionStore;
  let payload: DiscordSessionPayload | null;
  try {
    runtimeStore = store ?? createDiscordSessionStore(env);
    payload = await readDiscordSessionPayload(request, config, runtimeStore);
  } catch {
    return {
      ok: false,
      status: 503,
      reason: "verification_unavailable",
      clearCookie: false,
    };
  }
  const nowSeconds = Math.floor(now / 1000);
  if (!payload || payload.exp <= nowSeconds) {
    return {
      ok: false,
      status: 401,
      reason: "unauthenticated",
      clearCookie: Boolean(payload),
    };
  }

  if (nowSeconds - payload.membershipCheckedAt < DISCORD_MEMBERSHIP_RECHECK_SECONDS) {
    return {
      ok: true,
      viewer: viewerFrom(payload),
      cookieValue: null,
      cookieMaxAgeSeconds: Math.max(0, payload.exp - nowSeconds),
    };
  }

  try {
    if (payload.tokenExpiresAt <= nowSeconds + TOKEN_REFRESH_MARGIN_SECONDS) {
      const refreshed = await refreshDiscordAccessToken(payload.refreshToken, config);
      payload = {
        ...payload,
        accessToken: refreshed.accessToken,
        refreshToken: refreshed.refreshToken,
        tokenExpiresAt: nowSeconds + refreshed.expiresIn,
      };
    }
    const member = await fetchDiscordGuildMember(
      payload.accessToken,
      config.guildId,
    );
    const nextPayload: DiscordSessionPayload = {
      ...payload,
      ...member,
      membershipCheckedAt: nowSeconds,
    };
    const sessionId = readCookie(request, DISCORD_SESSION_COOKIE);
    if (!sessionId) {
      return {
        ok: false,
        status: 401,
        reason: "unauthenticated",
        clearCookie: false,
      };
    }
    await runtimeStore.write(sessionId, nextPayload);
    return {
      ok: true,
      viewer: viewerFrom(nextPayload),
      cookieValue: null,
      cookieMaxAgeSeconds: Math.max(0, nextPayload.exp - nowSeconds),
    };
  } catch (caught) {
    if (
      caught instanceof DiscordMembershipError &&
      [401, 403, 404].includes(caught.status)
    ) {
      const sessionId = readCookie(request, DISCORD_SESSION_COOKIE);
      if (sessionId) {
        try {
          await runtimeStore.delete(sessionId);
        } catch {
          // Clearing the opaque browser identifier still revokes this browser.
        }
      }
      return {
        ok: false,
        status: 403,
        reason: "not_a_member",
        clearCookie: true,
      };
    }
    return {
      ok: false,
      status: 503,
      reason: "verification_unavailable",
      clearCookie: false,
    };
  }
}

export function discordOAuthStateCookie(value: string): string {
  return serializeSecureCookie(
    DISCORD_OAUTH_STATE_COOKIE,
    value,
    OAUTH_STATE_TTL_SECONDS,
  );
}

export function discordSessionCookie(
  value: string,
  maxAgeSeconds: number,
): string {
  return serializeSecureCookie(
    DISCORD_SESSION_COOKIE,
    value,
    maxAgeSeconds,
  );
}

export function expireDiscordOAuthStateCookie(): string {
  return expireSecureCookie(DISCORD_OAUTH_STATE_COOKIE);
}

export function expireDiscordSessionCookie(): string {
  return expireSecureCookie(DISCORD_SESSION_COOKIE);
}

export async function deleteDiscordSession(
  request: Request,
  env: Environment = runtimeEnvironment(),
  store?: DiscordSessionStore,
): Promise<void> {
  const sessionId = readCookie(request, DISCORD_SESSION_COOKIE);
  if (!sessionId || !/^[A-Za-z0-9_-]{43,128}$/.test(sessionId)) return;
  try {
    await (store ?? createDiscordSessionStore(env)).delete(sessionId);
  } catch {
    // Logout still clears the only browser credential. Store expiry is the
    // fallback if the private service is temporarily unreachable.
  }
}

async function readDiscordSessionPayload(
  request: Request,
  config: DiscordAuthConfig,
  store: DiscordSessionStore,
): Promise<DiscordSessionPayload | null> {
  const value = readCookie(request, DISCORD_SESSION_COOKIE);
  if (!value) return null;
  if (!/^[A-Za-z0-9_-]{43,128}$/.test(value)) return null;
  const payload = await store.read(value);
  if (!isDiscordSessionPayload(payload)) return null;
  if (payload.guildId !== config.guildId) return null;
  return payload;
}

function viewerFrom(payload: DiscordSessionPayload): DiscordViewer {
  return {
    discordId: payload.discordId,
    username: payload.username,
    displayName: payload.displayName,
    membershipCheckedAt: payload.membershipCheckedAt,
    expiresAt: payload.exp,
  };
}

function isDiscordOAuthState(value: unknown): value is DiscordOAuthState {
  if (!isRecord(value)) return false;
  return (
    value.v === 1 &&
    typeof value.state === "string" &&
    /^[A-Za-z0-9_-]{32,}$/.test(value.state) &&
    typeof value.returnTo === "string" &&
    safeDiscordReturnTo(value.returnTo) === value.returnTo &&
    Number.isSafeInteger(value.exp)
  );
}

function isDiscordSessionPayload(value: unknown): value is DiscordSessionPayload {
  if (!isRecord(value)) return false;
  return (
    value.v === 1 &&
    typeof value.discordId === "string" &&
    /^[1-9]\d*$/.test(value.discordId) &&
    typeof value.username === "string" &&
    value.username.length > 0 &&
    value.username.length <= 100 &&
    typeof value.displayName === "string" &&
    value.displayName.length > 0 &&
    value.displayName.length <= 100 &&
    typeof value.guildId === "string" &&
    /^[1-9]\d*$/.test(value.guildId) &&
    typeof value.accessToken === "string" &&
    value.accessToken.length > 0 &&
    value.accessToken.length <= 4_096 &&
    typeof value.refreshToken === "string" &&
    value.refreshToken.length > 0 &&
    value.refreshToken.length <= 4_096 &&
    Number.isSafeInteger(value.tokenExpiresAt) &&
    Number.isSafeInteger(value.membershipCheckedAt) &&
    Number.isSafeInteger(value.exp)
  );
}

function randomToken(byteLength: number): string {
  const bytes = crypto.getRandomValues(new Uint8Array(byteLength));
  return base64UrlEncode(bytes);
}

function base64UrlEncode(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return isRecord(value) ? value : null;
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

function nullableStringField(
  value: Record<string, unknown>,
  key: string,
  maxLength: number,
): string | null {
  const field = value[key];
  return typeof field === "string" && field.trim() && field.length <= maxLength
    ? field.trim()
    : null;
}

function finiteInteger(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0
    ? value
    : null;
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

async function readJsonObject(
  response: Response,
): Promise<Record<string, unknown>> {
  const text = await response.text();
  if (text.length > 1_000_000) return {};
  try {
    const value: unknown = JSON.parse(text);
    return isRecord(value) ? value : {};
  } catch {
    return {};
  }
}

function isLoopbackHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]";
}
