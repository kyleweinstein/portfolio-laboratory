import {
  buildGitHubAuthorizationUrl,
  createOAuthState,
  getGitHubAuthConfig,
  oauthStateCookie,
  resolveGitHubCallbackUrl,
  safeReturnTo,
} from "../../../../github-auth";
import {
  isWebullIntegrationEnabled,
  jsonResponse,
} from "../../../../webull-server";

export async function GET(request: Request) {
  if (!isWebullIntegrationEnabled()) {
    return jsonResponse({ error: "The Webull integration is disabled." }, 404);
  }
  try {
    const config = getGitHubAuthConfig();
    const callbackUrl = resolveGitHubCallbackUrl(request);
    const searchParams = new URL(request.url).searchParams;
    const returnTo = safeReturnTo(
      searchParams.get("return_to") ?? searchParams.get("returnTo"),
    );
    const oauthState = await createOAuthState(returnTo, config.sessionSecret);
    return new Response(null, {
      status: 302,
      headers: {
        "cache-control": "no-store, max-age=0",
        location: buildGitHubAuthorizationUrl(
          config,
          callbackUrl,
          oauthState.state,
        ),
        "set-cookie": oauthStateCookie(oauthState.cookieValue),
      },
    });
  } catch {
    return jsonResponse({ error: "GitHub OAuth is not configured." }, 503);
  }
}
