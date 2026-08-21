import assert from "node:assert/strict";
import test from "node:test";
import {
  GITHUB_CSRF_HEADER,
  GITHUB_OAUTH_STATE_COOKIE,
  GITHUB_SESSION_COOKIE,
  createGitHubSession,
  createOAuthState,
  getGitHubAuthConfig,
  oauthStateCookie,
  parseGitHubOwnerIds,
  readGitHubSession,
  readOAuthState,
  safeReturnTo,
  serializeSecureCookie,
  validateMutationRequest,
} from "../app/github-auth.ts";

const TEST_SECRET = "test-session-secret-that-is-longer-than-thirty-two-bytes";
const TEST_ENV = {
  GITHUB_CLIENT_ID: "client-id",
  GITHUB_CLIENT_SECRET: "client-secret",
  GITHUB_SESSION_SECRET: TEST_SECRET,
  GITHUB_OWNER_IDS: "12345,67890",
};

test("owner allowlist uses stable positive numeric GitHub IDs", () => {
  assert.deepEqual([...parseGitHubOwnerIds("123, 456,123")], ["123", "456"]);
  assert.throws(() => parseGitHubOwnerIds("github-login"), /numeric GitHub user IDs/);
  assert.throws(() => parseGitHubOwnerIds("0"), /numeric GitHub user IDs/);
  const config = getGitHubAuthConfig(TEST_ENV);
  assert.equal(config.ownerIds.has("12345"), true);
  assert.equal(config.ownerIds.has("someone"), false);
});

test("OAuth state cookie is signed, short-lived, and carries only a safe return path", async () => {
  const now = Date.UTC(2026, 7, 20, 12, 0, 0);
  const state = await createOAuthState("https://attacker.example/", TEST_SECRET, now);
  const request = new Request("https://portfolio.example/api/webull/auth/callback", {
    headers: { cookie: `${GITHUB_OAUTH_STATE_COOKIE}=${state.cookieValue}` },
  });
  const payload = await readOAuthState(request, TEST_SECRET, now + 1_000);
  assert.equal(payload?.returnTo, "/");
  assert.equal(payload?.state, state.state);

  const last = state.cookieValue.at(-1);
  const tampered = `${state.cookieValue.slice(0, -1)}${last === "A" ? "B" : "A"}`;
  const tamperedRequest = new Request(request.url, {
    headers: { cookie: `${GITHUB_OAUTH_STATE_COOKIE}=${tampered}` },
  });
  assert.equal(await readOAuthState(tamperedRequest, TEST_SECRET, now), null);
  assert.equal(await readOAuthState(request, TEST_SECRET, now + 11 * 60_000), null);
});

test("signed owner session rejects tampering, expiry, and IDs removed from the allowlist", async () => {
  const now = Date.UTC(2026, 7, 20, 12, 0, 0);
  const config = getGitHubAuthConfig(TEST_ENV);
  const created = await createGitHubSession(
    { id: "12345", login: "portfolio-owner" },
    config,
    now,
  );
  const request = sessionRequest(created.cookieValue);
  const session = await readGitHubSession(request, TEST_ENV, now + 1_000);
  assert.equal(session?.githubId, "12345");
  assert.equal(session?.login, "portfolio-owner");
  assert.match(session?.csrfToken ?? "", /^[A-Za-z0-9_-]{32,}$/);

  const removedOwnerEnv = { ...TEST_ENV, GITHUB_OWNER_IDS: "67890" };
  assert.equal(await readGitHubSession(request, removedOwnerEnv, now), null);
  assert.equal(
    await readGitHubSession(request, TEST_ENV, now + 13 * 60 * 60_000),
    null,
  );
});

test("mutation guard requires exact same origin and the session CSRF token", async () => {
  const config = getGitHubAuthConfig(TEST_ENV);
  const { session } = await createGitHubSession(
    { id: "12345", login: "portfolio-owner" },
    config,
  );
  const valid = new Request("https://portfolio.example/api/webull/sync", {
    method: "POST",
    headers: {
      origin: "https://portfolio.example",
      "sec-fetch-site": "same-origin",
      [GITHUB_CSRF_HEADER]: session.csrfToken,
    },
  });
  assert.deepEqual(validateMutationRequest(valid, session), { ok: true });

  const crossOrigin = new Request(valid.url, {
    method: "POST",
    headers: {
      origin: "https://attacker.example",
      [GITHUB_CSRF_HEADER]: session.csrfToken,
    },
  });
  assert.equal(validateMutationRequest(crossOrigin, session).ok, false);

  const missingToken = new Request(valid.url, {
    method: "POST",
    headers: { origin: "https://portfolio.example" },
  });
  assert.equal(validateMutationRequest(missingToken, session).ok, false);
});

test("auth cookies are host-only, secure, HttpOnly, and same-site", () => {
  const state = oauthStateCookie("signed-state");
  assert.match(state, new RegExp(`^${GITHUB_OAUTH_STATE_COOKIE}=`));
  assert.match(state, /Path=\//);
  assert.match(state, /HttpOnly/);
  assert.match(state, /Secure/);
  assert.match(state, /SameSite=Lax/);
  assert.doesNotMatch(state, /Domain=/i);

  const session = serializeSecureCookie(GITHUB_SESSION_COOKIE, "signed", 60);
  assert.match(session, /Max-Age=60/);
});

test("return paths cannot escape the portfolio origin or loop through auth", () => {
  assert.equal(safeReturnTo("/portfolio?tab=webull#top"), "/portfolio?tab=webull#top");
  assert.equal(safeReturnTo("//attacker.example/path"), "/");
  assert.equal(safeReturnTo("https://attacker.example/path"), "/");
  assert.equal(safeReturnTo("/api/webull/auth/callback"), "/");
});

function sessionRequest(cookieValue) {
  return new Request("https://portfolio.example/api/webull/status", {
    headers: { cookie: `${GITHUB_SESSION_COOKIE}=${cookieValue}` },
  });
}
