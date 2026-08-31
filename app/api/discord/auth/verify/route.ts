import {
  discordSessionCookie,
  expireDiscordSessionCookie,
  revalidateDiscordSession,
  safeDiscordReturnTo,
} from "../../../../discord-auth";

export async function GET(request: Request) {
  const returnTo = safeDiscordReturnTo(
    new URL(request.url).searchParams.get("return_to"),
  );
  const result = await revalidateDiscordSession(request);
  if (!result.ok) {
    if (result.reason === "unauthenticated") {
      const response = Response.redirect(
        new URL(
          `/api/discord/auth/login?return_to=${encodeURIComponent(returnTo)}`,
          request.url,
        ),
        303,
      );
      if (result.clearCookie) {
        response.headers.append("set-cookie", expireDiscordSessionCookie());
      }
      return response;
    }
    const response = Response.json(
      {
        error: result.reason === "not_a_member"
          ? "Discord server membership is required."
          : "Discord membership could not be verified. Try again shortly.",
      },
      {
        status: result.status,
        headers: { "cache-control": "private, no-store" },
      },
    );
    if (result.clearCookie) {
      response.headers.append("set-cookie", expireDiscordSessionCookie());
    }
    return response;
  }

  const response = Response.redirect(new URL(returnTo, request.url), 303);
  response.headers.set("cache-control", "private, no-store, max-age=0");
  if (result.cookieValue) {
    response.headers.append(
      "set-cookie",
      discordSessionCookie(result.cookieValue, result.cookieMaxAgeSeconds),
    );
  }
  return response;
}
