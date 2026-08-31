import {
  createDiscordSession,
  DiscordMembershipError,
  discordSessionCookie,
  exchangeDiscordCode,
  expireDiscordOAuthStateCookie,
  fetchDiscordGuildMember,
  getDiscordAuthConfig,
  readDiscordOAuthState,
  resolveDiscordCallbackUrl,
} from "../../../../discord-auth";

export async function GET(request: Request) {
  let config: ReturnType<typeof getDiscordAuthConfig>;
  try {
    config = getDiscordAuthConfig();
  } catch {
    return errorResponse("Discord sign-in is not configured.", 503);
  }

  const url = new URL(request.url);
  const stateCookie = await readDiscordOAuthState(
    request,
    config.sessionSecret,
  );
  const state = url.searchParams.get("state");
  const code = url.searchParams.get("code");
  if (url.searchParams.get("error")) {
    return expireState(errorResponse("Discord authorization was cancelled.", 400));
  }
  if (!stateCookie || !state || stateCookie.state !== state || !code) {
    return expireState(
      errorResponse("The Discord OAuth state is invalid or expired.", 400),
    );
  }

  try {
    const callbackUrl = resolveDiscordCallbackUrl(request);
    const token = await exchangeDiscordCode(code, callbackUrl, config);
    const member = await fetchDiscordGuildMember(
      token.accessToken,
      config.guildId,
    );
    const session = await createDiscordSession(member, token, config);
    const response = new Response(null, {
      status: 303,
      headers: {
        "cache-control": "private, no-store, max-age=0",
        location: stateCookie.returnTo,
      },
    });
    response.headers.append(
      "set-cookie",
      discordSessionCookie(session.cookieValue, config.sessionTtlSeconds),
    );
    response.headers.append("set-cookie", expireDiscordOAuthStateCookie());
    return response;
  } catch (caught) {
    const membershipDenied = caught instanceof DiscordMembershipError &&
      [401, 403, 404].includes(caught.status);
    return expireState(
      errorResponse(
        membershipDenied
          ? "Discord server membership is required. Join the configured server and complete membership screening before trying again."
          : "Discord membership could not be verified. Try again shortly.",
        membershipDenied ? 403 : 503,
      ),
    );
  }
}

function errorResponse(message: string, status: number): Response {
  return Response.json(
    { error: message },
    { status, headers: { "cache-control": "private, no-store" } },
  );
}

function expireState(response: Response): Response {
  response.headers.append("set-cookie", expireDiscordOAuthStateCookie());
  return response;
}
