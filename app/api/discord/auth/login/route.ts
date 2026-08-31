import {
  buildDiscordAuthorizationUrl,
  createDiscordOAuthState,
  discordOAuthStateCookie,
  getDiscordAuthConfig,
  resolveDiscordCallbackUrl,
  safeDiscordReturnTo,
} from "../../../../discord-auth";

export async function GET(request: Request) {
  try {
    const config = getDiscordAuthConfig();
    const callbackUrl = resolveDiscordCallbackUrl(request);
    const url = new URL(request.url);
    const returnTo = safeDiscordReturnTo(
      url.searchParams.get("return_to") ?? url.searchParams.get("returnTo"),
    );
    const oauthState = await createDiscordOAuthState(
      returnTo,
      config.sessionSecret,
    );
    return new Response(null, {
      status: 302,
      headers: {
        "cache-control": "private, no-store, max-age=0",
        location: buildDiscordAuthorizationUrl(
          config,
          callbackUrl,
          oauthState.state,
        ),
        "set-cookie": discordOAuthStateCookie(oauthState.cookieValue),
      },
    });
  } catch {
    return Response.json(
      { error: "Discord sign-in is not configured." },
      { status: 503, headers: { "cache-control": "private, no-store" } },
    );
  }
}
