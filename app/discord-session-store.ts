import type { Environment } from "./github-auth.ts";

export type DiscordPrivateSession = {
  v: 1;
  discordId: string;
  username: string;
  displayName: string;
  guildId: string;
  accessToken: string;
  refreshToken: string;
  tokenExpiresAt: number;
  membershipCheckedAt: number;
  exp: number;
};

export interface DiscordSessionStore {
  write(sessionId: string, session: DiscordPrivateSession): Promise<void>;
  read(sessionId: string): Promise<DiscordPrivateSession | null>;
  delete(sessionId: string): Promise<void>;
}

export class DiscordSessionStoreUnavailableError extends Error {
  constructor() {
    super("The private Discord session store is unavailable.");
    this.name = "DiscordSessionStoreUnavailableError";
  }
}

export function createDiscordSessionStore(
  env: Environment = runtimeEnvironment(),
): DiscordSessionStore {
  return new BrokerServiceDiscordSessionStore(serviceConfig(env));
}

export function createMemoryDiscordSessionStore(): DiscordSessionStore {
  const sessions = new Map<string, DiscordPrivateSession>();
  return {
    async write(sessionId, session) {
      sessions.set(sessionId, structuredClone(session));
    },
    async read(sessionId) {
      const value = sessions.get(sessionId);
      return value ? structuredClone(value) : null;
    },
    async delete(sessionId) {
      sessions.delete(sessionId);
    },
  };
}

type ServiceConfig = {
  baseUrl: URL;
  internalToken: string;
  ownerId: string;
};

class BrokerServiceDiscordSessionStore implements DiscordSessionStore {
  private readonly config: ServiceConfig;

  constructor(config: ServiceConfig) {
    this.config = config;
  }

  async write(
    sessionId: string,
    session: DiscordPrivateSession,
  ): Promise<void> {
    await this.call("write", {
      sessionId,
      session: {
        discordId: session.discordId,
        username: session.username,
        displayName: session.displayName,
        guildId: session.guildId,
        accessToken: session.accessToken,
        refreshToken: session.refreshToken,
        tokenExpiresAt: secondsToIso(session.tokenExpiresAt),
        membershipCheckedAt: secondsToIso(session.membershipCheckedAt),
        expiresAt: secondsToIso(session.exp),
      },
    });
  }

  async read(sessionId: string): Promise<DiscordPrivateSession | null> {
    const payload = await this.call("read", { sessionId });
    const root = asRecord(payload);
    if (!root || root.session === null) return null;
    const session = asRecord(root.session);
    if (!session) throw new DiscordSessionStoreUnavailableError();
    const normalized: DiscordPrivateSession = {
      v: 1,
      discordId: requiredString(session.discordId, 32),
      username: requiredString(session.username, 100),
      displayName: requiredString(session.displayName, 100),
      guildId: requiredString(session.guildId, 32),
      accessToken: requiredString(session.accessToken, 4_096),
      refreshToken: requiredString(session.refreshToken, 4_096),
      tokenExpiresAt: isoToSeconds(session.tokenExpiresAt),
      membershipCheckedAt: isoToSeconds(session.membershipCheckedAt),
      exp: isoToSeconds(session.expiresAt),
    };
    return normalized;
  }

  async delete(sessionId: string): Promise<void> {
    await this.call("delete", { sessionId });
  }

  private async call(action: string, body: unknown): Promise<unknown> {
    let response: Response;
    try {
      response = await fetch(
        new URL(`v1/discord-sessions/${action}`, this.config.baseUrl),
        {
          method: "POST",
          headers: {
            accept: "application/json",
            authorization: `Bearer ${this.config.internalToken}`,
            "content-type": "application/json",
            "x-portfolio-owner-github-id": this.config.ownerId,
          },
          body: JSON.stringify(body),
          redirect: "error",
          signal: AbortSignal.timeout(10_000),
        },
      );
    } catch {
      throw new DiscordSessionStoreUnavailableError();
    }
    const text = await response.text();
    if (!response.ok || text.length > 100_000) {
      throw new DiscordSessionStoreUnavailableError();
    }
    try {
      return text ? JSON.parse(text) as unknown : {};
    } catch {
      throw new DiscordSessionStoreUnavailableError();
    }
  }
}

function serviceConfig(env: Environment): ServiceConfig {
  const rawUrl = env.PORTFOLIO_SERVICE_URL?.trim() ??
    env.BROKER_SERVICE_URL?.trim() ??
    env.WEBULL_SERVICE_URL?.trim() ??
    "";
  const internalToken = env.PORTFOLIO_INTERNAL_TOKEN ??
    env.BROKER_INTERNAL_TOKEN ??
    env.WEBULL_INTERNAL_TOKEN ??
    "";
  const ownerId = env.PORTFOLIO_OWNER_GITHUB_ID?.trim() ??
    env.GITHUB_OWNER_ID?.trim() ??
    env.GITHUB_OWNER_IDS?.split(",")[0]?.trim() ??
    "";
  let baseUrl: URL;
  try {
    baseUrl = new URL(rawUrl);
  } catch {
    throw new DiscordSessionStoreUnavailableError();
  }
  if (
    !["http:", "https:"].includes(baseUrl.protocol) ||
    baseUrl.username ||
    baseUrl.password ||
    !isPrivateServiceHost(baseUrl.hostname) ||
    (baseUrl.pathname !== "/" && baseUrl.pathname !== "") ||
    internalToken.length < 16 ||
    !/^[1-9]\d*$/.test(ownerId)
  ) {
    throw new DiscordSessionStoreUnavailableError();
  }
  baseUrl.search = "";
  baseUrl.hash = "";
  if (!baseUrl.pathname.endsWith("/")) baseUrl.pathname += "/";
  return { baseUrl, internalToken, ownerId };
}

function secondsToIso(value: number): string {
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new DiscordSessionStoreUnavailableError();
  }
  return new Date(value * 1_000).toISOString();
}

function isoToSeconds(value: unknown): number {
  if (typeof value !== "string") throw new DiscordSessionStoreUnavailableError();
  const milliseconds = Date.parse(value);
  if (!Number.isFinite(milliseconds)) throw new DiscordSessionStoreUnavailableError();
  return Math.floor(milliseconds / 1_000);
}

function requiredString(value: unknown, maximum: number): string {
  if (typeof value !== "string" || value.length < 1 || value.length > maximum) {
    throw new DiscordSessionStoreUnavailableError();
  }
  return value;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function runtimeEnvironment(): Environment {
  return typeof process === "undefined" ? {} : process.env;
}

function isPrivateServiceHost(hostname: string): boolean {
  const normalized = hostname.toLowerCase();
  return normalized === "localhost" ||
    normalized === "127.0.0.1" ||
    normalized === "[::1]" ||
    normalized.endsWith(".railway.internal");
}
