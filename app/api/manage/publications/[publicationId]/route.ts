import { authorizePublicationOwner, ownerJsonResponse } from "../../../../owner-publication-server";
import {
  publishManagedPublication,
  unpublishManagedPublication,
} from "../../../../publication-server";

export async function POST(
  request: Request,
  context: { params: Promise<{ publicationId: string }> },
) {
  const access = await authorizePublicationOwner(request, true);
  if (!access.ok) return access.response;
  const { publicationId } = await context.params;
  try {
    return ownerJsonResponse(await publishManagedPublication(publicationId));
  } catch {
    return ownerJsonResponse(
      { error: "The portfolio could not be published. Its previous revision is unchanged." },
      502,
    );
  }
}

export async function DELETE(
  request: Request,
  context: { params: Promise<{ publicationId: string }> },
) {
  const access = await authorizePublicationOwner(request, true);
  if (!access.ok) return access.response;
  const { publicationId } = await context.params;
  try {
    return ownerJsonResponse(await unpublishManagedPublication(publicationId));
  } catch {
    return ownerJsonResponse(
      { error: "The portfolio could not be unpublished. Its previous revision is unchanged." },
      502,
    );
  }
}
