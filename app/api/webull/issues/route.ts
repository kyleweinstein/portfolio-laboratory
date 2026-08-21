import {
  authorizeWebullOwner,
  proxyWebullJson,
} from "../../../webull-server";

export async function GET(request: Request) {
  const access = await authorizeWebullOwner(request);
  if (!access.ok) return access.response;
  return proxyWebullJson("/issues", access.session);
}
