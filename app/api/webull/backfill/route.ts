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
    const body: Record<string, unknown> = {};
    if ("accountId" in input) {
      if (
        typeof input.accountId !== "string" ||
        !/^[A-Za-z0-9_-]{1,128}$/.test(input.accountId)
      ) {
        return jsonResponse({ error: "Select a valid Webull account." }, 400);
      }
      body.accountId = input.accountId;
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
    return proxyWebullJson("/backfill", access.session, {
      method: "POST",
      body,
      timeoutMs: 60_000,
    });
  } catch (caught) {
    return requestBodyErrorResponse(caught);
  }
}
