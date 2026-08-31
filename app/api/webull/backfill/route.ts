import {
  authorizeWebullOwner,
  jsonResponse,
  proxyWebullJson,
  readJsonBody,
  requestBodyErrorResponse,
  resolveWebullAccountReference,
  webullStatusResponse,
} from "../../../webull-server";

export async function POST(request: Request) {
  const access = await authorizeWebullOwner(request, { mutation: true });
  if (!access.ok) return access.response;
  try {
    const input = await readJsonBody(request);
    const body: Record<string, unknown> = {};
    if ("accountRef" in input) {
      if (
        typeof input.accountRef !== "string" ||
        !/^wbr_[A-Za-z0-9_-]{24,64}$/.test(input.accountRef)
      ) {
        return jsonResponse({ error: "Select a valid Webull account." }, 400);
      }
      const accountId = await resolveWebullAccountReference(input.accountRef, access.session);
      if (!accountId) {
        return jsonResponse({ error: "Select a valid Webull account." }, 400);
      }
      body.accountId = accountId;
    }
    if ("days" in input) {
      if (
        typeof input.days !== "number" ||
        !Number.isSafeInteger(input.days) ||
        input.days < 1 ||
        input.days > 3650
      ) {
        return jsonResponse({ error: "days must be an integer from 1 to 3650." }, 400);
      }
      body.days = input.days;
    }
    const response = await proxyWebullJson("/backfill", access.session, {
      method: "POST",
      body,
      timeoutMs: 60_000,
    });
    return response.ok ? webullStatusResponse(request) : response;
  } catch (caught) {
    return requestBodyErrorResponse(caught);
  }
}
