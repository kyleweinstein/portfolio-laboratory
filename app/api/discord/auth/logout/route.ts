import {
  deleteDiscordSession,
  expireDiscordOAuthStateCookie,
  expireDiscordSessionCookie,
  safeDiscordReturnTo,
} from "../../../../discord-auth";

export async function GET(request: Request) {
  await deleteDiscordSession(request);
  const returnTo = safeDiscordReturnTo(
    new URL(request.url).searchParams.get("return_to"),
  );
  const response = Response.redirect(new URL(returnTo, request.url), 303);
  response.headers.set("cache-control", "private, no-store, max-age=0");
  response.headers.append("set-cookie", expireDiscordSessionCookie());
  response.headers.append("set-cookie", expireDiscordOAuthStateCookie());
  return response;
}
