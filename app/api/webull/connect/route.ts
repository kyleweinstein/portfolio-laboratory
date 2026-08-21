import {
  authorizeWebullOwner,
  proxyWebullJson,
} from "../../../webull-server";

export async function POST(request: Request) {
  const access = await authorizeWebullOwner(request, { mutation: true });
  if (!access.ok) return access.response;
  return proxyWebullJson("/connect", access.session, {
    method: "POST",
    body: {},
  });
}

export async function DELETE(request: Request) {
  const access = await authorizeWebullOwner(request, { mutation: true });
  if (!access.ok) return access.response;
  return proxyWebullJson("/connect", access.session, { method: "DELETE" });
}
