import {
  forwardVerifiedPlaidWebhook,
  PublicationServiceError,
} from "../../../publication-server";

export const dynamic = "force-dynamic";

const MAX_WEBHOOK_BYTES = 131_072;

export async function POST(request: Request): Promise<Response> {
  const contentLength = Number(request.headers.get("content-length") ?? "0");
  if (Number.isFinite(contentLength) && contentLength > MAX_WEBHOOK_BYTES) {
    return webhookResponse({ accepted: false }, 413);
  }
  const signature = request.headers.get("plaid-verification") ?? "";
  const rawBody = await request.text();
  if (new TextEncoder().encode(rawBody).byteLength > MAX_WEBHOOK_BYTES) {
    return webhookResponse({ accepted: false }, 413);
  }
  try {
    await forwardVerifiedPlaidWebhook(rawBody, signature);
    // The private service verifies the signed raw body. Scheduled sync is the
    // durable refetch path, so this receiver stays comfortably under Plaid's
    // webhook response deadline and remains idempotent.
    return webhookResponse({ accepted: true }, 200);
  } catch (caught) {
    const status = caught instanceof PublicationServiceError && caught.status === 503
      ? 503
      : 401;
    return webhookResponse({ accepted: false }, status);
  }
}

function webhookResponse(body: { accepted: boolean }, status: number): Response {
  return Response.json(body, {
    status,
    headers: {
      "cache-control": "no-store, max-age=0",
      "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
      "cross-origin-resource-policy": "same-site",
      "referrer-policy": "no-referrer",
    },
  });
}
