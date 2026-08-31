import {
  GITHUB_OAUTH_STATE_COOKIE,
  GITHUB_SESSION_COOKIE,
  expireSecureCookie,
  safeReturnTo,
} from "../../../../github-auth";

export async function GET(request: Request) {
  const returnTo = safeReturnTo(
    new URL(request.url).searchParams.get("return_to"),
  );
  const response = Response.redirect(new URL(returnTo, request.url), 303);
  response.headers.set("cache-control", "private, no-store, max-age=0");
  response.headers.append(
    "set-cookie",
    expireSecureCookie(GITHUB_SESSION_COOKIE),
  );
  response.headers.append(
    "set-cookie",
    expireSecureCookie(GITHUB_OAUTH_STATE_COOKIE),
  );
  return response;
}
