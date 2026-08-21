import {
  authorizeWebullOwner,
  jsonResponse,
  proxyWebullJson,
  readJsonBody,
  requestBodyErrorResponse,
} from "../../../webull-server";

export async function POST(request: Request) {
  const access = await authorizeWebullOwner(request, { mutation: true });
  if (!access.ok) return access.response;
  try {
    const input = await readJsonBody(request);
    const accountId = input.accountId;
    if (
      accountId !== undefined &&
      (typeof accountId !== "string" ||
        !/^[A-Za-z0-9_-]{1,128}$/.test(accountId))
    ) {
      return jsonResponse({ error: "Select a valid Webull account." }, 400);
    }
    return proxyWebullJson("/sync", access.session, {
      method: "POST",
      body: accountId ? { accountId } : {},
      timeoutMs: 60_000,
    });
  } catch (caught) {
    return requestBodyErrorResponse(caught);
  }
}
