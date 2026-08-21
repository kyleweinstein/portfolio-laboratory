import {
  authorizeWebullOwner,
  proxyWebullJson,
  webullStatusResponse,
} from "../../../webull-server";

export async function POST(request: Request) {
  const access = await authorizeWebullOwner(request, { mutation: true });
  if (!access.ok) return access.response;
  const response = await proxyWebullJson("/connect", access.session, {
    method: "POST",
    body: {},
  });
  return response.ok ? webullStatusResponse(request) : response;
}

export async function DELETE(request: Request) {
  const access = await authorizeWebullOwner(request, { mutation: true });
  if (!access.ok) return access.response;
  const response = await proxyWebullJson("/connect", access.session, {
    method: "DELETE",
  });
  return response.ok ? webullStatusResponse(request) : response;
}
