import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  DISCORD_MEMBERSHIP_RECHECK_SECONDS,
  DISCORD_OAUTH_STATE_COOKIE,
  DISCORD_SESSION_COOKIE,
  buildDiscordAuthorizationUrl,
  createDiscordOAuthState,
  createDiscordSession,
  discordOAuthStateCookie,
  fetchDiscordGuildMember,
  getDiscordAuthConfig,
  inspectDiscordSession,
  readDiscordOAuthState,
  revalidateDiscordSession,
  safeDiscordReturnTo,
} from "../app/discord-auth.ts";
import {
  createDiscordSessionStore,
  createMemoryDiscordSessionStore,
} from "../app/discord-session-store.ts";

const NOW = Date.UTC(2026, 7, 30, 12, 0, 0);
const ENV = {
  DISCORD_CLIENT_ID: "123456789012345678",
  DISCORD_CLIENT_SECRET: "discord-client-secret",
  DISCORD_GUILD_ID: "987654321098765432",
  DISCORD_SESSION_SECRET: "discord-session-secret-that-is-at-least-thirty-two-bytes",
};
const MEMBER = {
  discordId: "111122223333444455",
  username: "member",
  displayName: "Portfolio Member",
};
const TOKEN = {
  accessToken: "discord-access-token-private",
  refreshToken: "discord-refresh-token-private",
  expiresIn: 3600,
};

test("Discord OAuth requests only identity and current-guild membership scopes", async () => {
  const config = getDiscordAuthConfig(ENV);
  const state = await createDiscordOAuthState("/portfolios/growth", config.sessionSecret, NOW);
  const authorization = new URL(buildDiscordAuthorizationUrl(
    config,
    "https://lab.example/api/discord/auth/callback",
    state.state,
  ));
  assert.equal(authorization.origin, "https://discord.com");
  assert.equal(authorization.searchParams.get("response_type"), "code");
  assert.equal(authorization.searchParams.get("scope"), "identify guilds.members.read");
  assert.equal(authorization.searchParams.get("state"), state.state);
  assert.doesNotMatch(authorization.toString(), /bot|applications\.commands/);

  const request = new Request("https://lab.example/api/discord/auth/callback", {
    headers: { cookie: `${DISCORD_OAUTH_STATE_COOKIE}=${state.cookieValue}` },
  });
  assert.equal((await readDiscordOAuthState(request, config.sessionSecret, NOW))?.returnTo, "/portfolios/growth");
  assert.equal(safeDiscordReturnTo("https://attacker.example"), "/portfolios");
  assert.equal(safeDiscordReturnTo("/api/discord/auth/callback"), "/portfolios");
  assert.match(discordOAuthStateCookie(state.cookieValue), /HttpOnly/);
});

test("Discord viewer session is opaque, tamper-evident, and rechecked every five minutes", async () => {
  const config = getDiscordAuthConfig(ENV);
  const store = createMemoryDiscordSessionStore();
  const session = await createDiscordSession(MEMBER, TOKEN, config, NOW, store);
  assert.doesNotMatch(session.cookieValue, /discord-access-token-private|member|111122223333444455/);
  assert.match(session.cookieValue, /^[A-Za-z0-9_-]{43}$/);
  const request = sessionRequest(session.cookieValue);
  assert.equal((await inspectDiscordSession(request, ENV, NOW + 1_000, store)).state, "authenticated");
  assert.equal(
    (await inspectDiscordSession(request, ENV, NOW + DISCORD_MEMBERSHIP_RECHECK_SECONDS * 1_000, store)).state,
    "stale",
  );

  const tampered = `${session.cookieValue.slice(0, -1)}${session.cookieValue.endsWith("A") ? "B" : "A"}`;
  assert.equal((await inspectDiscordSession(sessionRequest(tampered), ENV, NOW, store)).state, "unauthenticated");

  const originalFetch = globalThis.fetch;
  let requestedAuthorization = "";
  globalThis.fetch = async (input, init) => {
    const url = String(input);
    assert.match(url, new RegExp(`/users/@me/guilds/${ENV.DISCORD_GUILD_ID}/member$`));
    requestedAuthorization = new Headers(init?.headers).get("authorization") ?? "";
    return Response.json({ user: { id: MEMBER.discordId, username: MEMBER.username, global_name: MEMBER.displayName }, pending: false });
  };
  try {
    const refreshed = await revalidateDiscordSession(
      request,
      ENV,
      NOW + (DISCORD_MEMBERSHIP_RECHECK_SECONDS + 1) * 1_000,
      store,
    );
    assert.equal(refreshed.ok, true);
    assert.equal(requestedAuthorization, `Bearer ${TOKEN.accessToken}`);
    assert.ok(refreshed.ok && refreshed.cookieValue === null);
    assert.equal(
      (await inspectDiscordSession(
        request,
        ENV,
        NOW + (DISCORD_MEMBERSHIP_RECHECK_SECONDS + 2) * 1_000,
        store,
      )).state,
      "authenticated",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Discord verification fails closed on outage and clears a former member session", async () => {
  const config = getDiscordAuthConfig(ENV);
  const store = createMemoryDiscordSessionStore();
  const session = await createDiscordSession(MEMBER, TOKEN, config, NOW, store);
  const request = sessionRequest(session.cookieValue);
  const staleNow = NOW + (DISCORD_MEMBERSHIP_RECHECK_SECONDS + 1) * 1_000;
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => { throw new Error("network unavailable"); };
    assert.deepEqual(await revalidateDiscordSession(request, ENV, staleNow, store), {
      ok: false,
      status: 503,
      reason: "verification_unavailable",
      clearCookie: false,
    });

    globalThis.fetch = async () => Response.json({ message: "Unknown Member" }, { status: 404 });
    assert.deepEqual(await revalidateDiscordSession(request, ENV, staleNow, store), {
      ok: false,
      status: 403,
      reason: "not_a_member",
      clearCookie: true,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("pending Discord members are denied", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => Response.json({
    pending: true,
    user: { id: MEMBER.discordId, username: MEMBER.username },
  });
  try {
    await assert.rejects(
      () => fetchDiscordGuildMember(TOKEN.accessToken, ENV.DISCORD_GUILD_ID),
      /membership screening/i,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Discord private session material can target only a private service host", () => {
  const serviceEnv = {
    PORTFOLIO_INTERNAL_TOKEN: "internal-token-long-enough",
    PORTFOLIO_OWNER_GITHUB_ID: "123",
  };
  assert.throws(
    () => createDiscordSessionStore({
      ...serviceEnv,
      PORTFOLIO_SERVICE_URL: "https://attacker.example/collect",
    }),
    /session store is unavailable/i,
  );
  assert.doesNotThrow(() => createDiscordSessionStore({
    ...serviceEnv,
    PORTFOLIO_SERVICE_URL: "http://broker.railway.internal:8000",
  }));
});

test("Discord member identity is not rendered into follower HTML", async () => {
  const sources = await Promise.all([
    readFile(new URL("../app/portfolios/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/portfolios/[slug]/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/publication-ui.tsx", import.meta.url), "utf8"),
  ]);
  assert.doesNotMatch(sources.join("\n"), /viewer\.displayName|viewerName/);
});

function sessionRequest(cookieValue) {
  return new Request("https://lab.example/api/portfolios", {
    headers: { cookie: `${DISCORD_SESSION_COOKIE}=${cookieValue}` },
  });
}
