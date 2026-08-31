export type PublishedMapPan = { x: number; y: number };

export type PublishedMapBounds = {
  left: number;
  top: number;
  width: number;
  height: number;
};

export type PublishedMapCell = { row: number; column: number };

export const PUBLISHED_MAP_MIN_ZOOM = 1;
export const PUBLISHED_MAP_MAX_ZOOM = 16;

export function clampPublishedMapZoom(value: number): number {
  return clamp(value, PUBLISHED_MAP_MIN_ZOOM, PUBLISHED_MAP_MAX_ZOOM);
}

export function clampPublishedMapPan(
  size: number,
  zoom: number,
  pan: PublishedMapPan,
): PublishedMapPan {
  const safeSize = Math.max(1, size);
  const safeZoom = clampPublishedMapZoom(zoom);
  const minimum = safeSize - safeSize * safeZoom;
  return {
    x: clamp(pan.x, minimum, 0),
    y: clamp(pan.y, minimum, 0),
  };
}

export function publishedMapCellFromPointer({
  clientX,
  clientY,
  bounds,
  size,
  count,
  zoom,
  pan,
}: {
  clientX: number;
  clientY: number;
  bounds: PublishedMapBounds;
  size: number;
  count: number;
  zoom: number;
  pan: PublishedMapPan;
}): PublishedMapCell | null {
  if (
    count < 1 ||
    size <= 0 ||
    bounds.width <= 0 ||
    bounds.height <= 0
  ) {
    return null;
  }
  const safeZoom = clampPublishedMapZoom(zoom);
  const safePan = clampPublishedMapPan(size, safeZoom, pan);
  const x = (clientX - bounds.left) * (size / bounds.width);
  const y = (clientY - bounds.top) * (size / bounds.height);
  if (x < 0 || y < 0 || x >= size || y >= size) return null;
  const cellSize = (size / count) * safeZoom;
  const column = Math.floor((x - safePan.x) / cellSize);
  const row = Math.floor((y - safePan.y) / cellSize);
  if (row < 0 || column < 0 || row >= count || column >= count) return null;
  return { row, column };
}

export function zoomPublishedMapAt(
  size: number,
  zoom: number,
  pan: PublishedMapPan,
  nextZoom: number,
  anchor: { x: number; y: number },
): { zoom: number; pan: PublishedMapPan } {
  const currentZoom = clampPublishedMapZoom(zoom);
  const clampedZoom = clampPublishedMapZoom(nextZoom);
  const safePan = clampPublishedMapPan(size, currentZoom, pan);
  const ratio = clampedZoom / currentZoom;
  return {
    zoom: clampedZoom,
    pan: clampPublishedMapPan(size, clampedZoom, {
      x: anchor.x - (anchor.x - safePan.x) * ratio,
      y: anchor.y - (anchor.y - safePan.y) * ratio,
    }),
  };
}

export function revealPublishedMapCell(
  size: number,
  count: number,
  zoom: number,
  pan: PublishedMapPan,
  row: number,
  column: number,
): PublishedMapPan {
  if (count < 1 || size <= 0) return { x: 0, y: 0 };
  const safeZoom = clampPublishedMapZoom(zoom);
  const safePan = clampPublishedMapPan(size, safeZoom, pan);
  const cellSize = (size / count) * safeZoom;
  const inset = Math.min(8, cellSize / 4);
  let x = safePan.x;
  let y = safePan.y;
  const left = x + column * cellSize;
  const right = left + cellSize;
  const top = y + row * cellSize;
  const bottom = top + cellSize;
  if (left < inset) x += inset - left;
  else if (right > size - inset) x -= right - (size - inset);
  if (top < inset) y += inset - top;
  else if (bottom > size - inset) y -= bottom - (size - inset);
  return clampPublishedMapPan(size, safeZoom, { x, y });
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}
