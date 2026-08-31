import {
  buildGitHubAuthorizationUrl,
  createOAuthState,
  getGitHubAuthConfig,
  oauthStateCookie,
  resolveGitHubOwnerCallbackUrl,
  safeReturnTo,
} from "../../../../github-auth";

export async function GET(request: Request) {
  try {
    const config = getGitHubAuthConfig();
    const callbackUrl = resolveGitHubOwnerCallbackUrl(request);
    const searchParams = new URL(request.url).searchParams;
    const returnTo = safeReturnTo(
      searchParams.get("return_to") ?? searchParams.get("returnTo"),
    );
    const oauthState = await createOAuthState(returnTo, config.sessionSecret);
    return new Response(null, {
      status: 302,
      headers: {
        "cache-control": "private, no-store, max-age=0",
        location: buildGitHubAuthorizationUrl(
          config,
          callbackUrl,
          oauthState.state,
        ),
        "set-cookie": oauthStateCookie(oauthState.cookieValue),
      },
    });
  } catch {
    return ownerAuthError("GitHub owner sign-in is not configured.", 503);
  }
}

function ownerAuthError(message: string, status: number): Response {
  return Response.json(
    { error: message },
    { status, headers: { "cache-control": "private, no-store" } },
  );
}
