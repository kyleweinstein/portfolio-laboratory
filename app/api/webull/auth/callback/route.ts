import {
  GITHUB_OAUTH_STATE_COOKIE,
  createGitHubSession,
  exchangeGitHubCode,
  expireSecureCookie,
  fetchGitHubIdentity,
  getGitHubAuthConfig,
  readOAuthState,
  resolveGitHubCallbackUrl,
  sessionCookie,
} from "../../../../github-auth";
import { jsonResponse } from "../../../../webull-server";

export async function GET(request: Request) {
  let config: ReturnType<typeof getGitHubAuthConfig>;
  try {
    config = getGitHubAuthConfig();
  } catch {
    return jsonResponse({ error: "GitHub OAuth is not configured." }, 503);
  }

  const url = new URL(request.url);
  const stateCookie = await readOAuthState(request, config.sessionSecret);
  const state = url.searchParams.get("state");
  const code = url.searchParams.get("code");
  const oauthError = url.searchParams.get("error");
  if (oauthError) {
    return expireState(
      jsonResponse({ error: "GitHub authorization was cancelled." }, 400),
    );
  }
  if (!stateCookie || !state || stateCookie.state !== state || !code) {
    return expireState(
      jsonResponse({ error: "The GitHub OAuth state is invalid or expired." }, 400),
    );
  }

  try {
    const callbackUrl = resolveGitHubCallbackUrl(request);
    const accessToken = await exchangeGitHubCode(code, callbackUrl, config);
    const identity = await fetchGitHubIdentity(accessToken);
    if (!config.ownerIds.has(identity.id)) {
      return expireState(
        jsonResponse({ error: "This GitHub account is not authorized." }, 403),
      );
    }
    const ownerSession = await createGitHubSession(identity, config);
    const response = new Response(null, {
      status: 303,
      headers: {
        "cache-control": "no-store, max-age=0",
        location: stateCookie.returnTo,
      },
    });
    response.headers.append(
      "set-cookie",
      sessionCookie(ownerSession.cookieValue, config),
    );
    response.headers.append(
      "set-cookie",
      expireSecureCookie(GITHUB_OAUTH_STATE_COOKIE),
    );
    return response;
  } catch {
    return expireState(
      jsonResponse({ error: "GitHub authentication could not be completed." }, 502),
    );
  }
}

function expireState(response: Response): Response {
  response.headers.append(
    "set-cookie",
    expireSecureCookie(GITHUB_OAUTH_STATE_COOKIE),
  );
  return response;
}
