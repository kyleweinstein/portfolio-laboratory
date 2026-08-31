import {
  GITHUB_OAUTH_STATE_COOKIE,
  createGitHubSession,
  exchangeGitHubCode,
  expireSecureCookie,
  fetchGitHubIdentity,
  getGitHubAuthConfig,
  readOAuthState,
  resolveGitHubOwnerCallbackUrl,
  sessionCookie,
} from "../../../../github-auth";

export async function GET(request: Request) {
  let config: ReturnType<typeof getGitHubAuthConfig>;
  try {
    config = getGitHubAuthConfig();
  } catch {
    return ownerAuthError("GitHub owner sign-in is not configured.", 503);
  }

  const url = new URL(request.url);
  const stateCookie = await readOAuthState(request, config.sessionSecret);
  const state = url.searchParams.get("state");
  const code = url.searchParams.get("code");
  if (url.searchParams.get("error")) {
    return expireState(ownerAuthError("GitHub authorization was cancelled.", 400));
  }
  if (!stateCookie || !state || stateCookie.state !== state || !code) {
    return expireState(
      ownerAuthError("The GitHub OAuth state is invalid or expired.", 400),
    );
  }

  try {
    const callbackUrl = resolveGitHubOwnerCallbackUrl(request);
    const accessToken = await exchangeGitHubCode(code, callbackUrl, config);
    const identity = await fetchGitHubIdentity(accessToken);
    if (!config.ownerIds.has(identity.id)) {
      return expireState(
        ownerAuthError("This GitHub account is not authorized.", 403),
      );
    }
    const ownerSession = await createGitHubSession(identity, config);
    const response = new Response(null, {
      status: 303,
      headers: {
        "cache-control": "private, no-store, max-age=0",
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
      ownerAuthError("GitHub owner authentication could not be completed.", 502),
    );
  }
}

function ownerAuthError(message: string, status: number): Response {
  return Response.json(
    { error: message },
    { status, headers: { "cache-control": "private, no-store" } },
  );
}

function expireState(response: Response): Response {
  response.headers.append(
    "set-cookie",
    expireSecureCookie(GITHUB_OAUTH_STATE_COOKIE),
  );
  return response;
}
