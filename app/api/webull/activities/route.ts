import {
  authorizeWebullOwner,
  proxyWebullJson,
} from "../../../webull-server";

const ALLOWED_QUERY_PARAMETERS = new Set([
  "activityTypes",
  "cursor",
  "endTime",
  "limit",
  "startTime",
]);

export async function GET(request: Request) {
  const access = await authorizeWebullOwner(request);
  if (!access.ok) return access.response;
  const incoming = new URL(request.url).searchParams;
  const outgoing = new URLSearchParams();
  for (const [key, value] of incoming) {
    if (!ALLOWED_QUERY_PARAMETERS.has(key) || value.length > 200) continue;
    if (key === "limit") {
      const limit = Number(value);
      if (!Number.isSafeInteger(limit) || limit < 1 || limit > 250) continue;
    }
    outgoing.append(key, value);
  }
  const query = outgoing.toString();
  return proxyWebullJson(
    `/activities${query ? `?${query}` : ""}`,
    access.session,
  );
}
