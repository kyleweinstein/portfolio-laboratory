import {
  authorizeWebullOwner,
  proxyWebullJson,
} from "../../../webull-server";

export async function POST(request: Request) {
  const access = await authorizeWebullOwner(request, { mutation: true });
  if (!access.ok) return access.response;
  return proxyWebullJson("/sync", access.session, {
    method: "POST",
    body: {},
    timeoutMs: 60_000,
  });
}
