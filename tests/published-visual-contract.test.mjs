import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  clampPublishedMapPan,
  publishedMapCellFromPointer,
  revealPublishedMapCell,
  zoomPublishedMapAt,
} from "../app/published-map-geometry.ts";
import { publicationChartSegments } from "../app/publication-chart.ts";

test("published map pointer coordinates account for CSS scaling, zoom, and pan", () => {
  assert.deepEqual(publishedMapCellFromPointer({
    clientX: 175,
    clientY: 275,
    bounds: { left: 100, top: 200, width: 200, height: 200 },
    size: 400,
    count: 4,
    zoom: 1,
    pan: { x: 0, y: 0 },
  }), { row: 1, column: 1 });

  assert.deepEqual(publishedMapCellFromPointer({
    clientX: 60,
    clientY: 170,
    bounds: { left: 10, top: 20, width: 200, height: 200 },
    size: 400,
    count: 4,
    zoom: 2,
    pan: { x: -200, y: -200 },
  }), { row: 2, column: 1 });
});

test("published map pan stays in bounds and zoom preserves its anchor", () => {
  assert.deepEqual(
    clampPublishedMapPan(400, 1, { x: -100, y: 80 }),
    { x: 0, y: 0 },
  );
  assert.deepEqual(
    clampPublishedMapPan(400, 2, { x: -900, y: 80 }),
    { x: -400, y: 0 },
  );
  assert.deepEqual(
    zoomPublishedMapAt(400, 1, { x: 0, y: 0 }, 2, { x: 100, y: 300 }),
    { zoom: 2, pan: { x: -100, y: -300 } },
  );
});

test("keyboard selection reveal keeps the selected zoomed cell addressable", () => {
  const size = 500;
  const count = 10;
  const zoom = 4;
  const pan = revealPublishedMapCell(
    size,
    count,
    zoom,
    { x: 0, y: 0 },
    9,
    9,
  );
  const cellSize = (size / count) * zoom;
  const clientX = pan.x + 9 * cellSize + cellSize / 2;
  const clientY = pan.y + 9 * cellSize + cellSize / 2;
  assert.deepEqual(publishedMapCellFromPointer({
    clientX,
    clientY,
    bounds: { left: 0, top: 0, width: size, height: size },
    size,
    count,
    zoom,
    pan,
  }), { row: 9, column: 9 });
});

test("performance chart creates disjoint polylines across missing benchmark dates", () => {
  const points = [0, 1, null, 3, 4].map((benchmarkReturnPercent, index) => ({
    date: `2026-01-0${index + 1}`,
    returnPercent: index,
    benchmarkReturnPercent,
  }));
  const bounds = {
    width: 100,
    height: 100,
    padding: 0,
    minimum: 0,
    maximum: 4,
  };
  assert.deepEqual(
    publicationChartSegments(points, "benchmarkReturnPercent", bounds),
    ["0.0,100.0 25.0,75.0", "75.0,25.0 100.0,0.0"],
  );
  assert.equal(
    publicationChartSegments(points, "returnPercent", bounds).length,
    1,
  );
});

test("published visual source keeps the required accessible interaction contract", async () => {
  const map = await readFile(
    new URL("../app/published-diversification-map.tsx", import.meta.url),
    "utf8",
  );
  const chart = await readFile(
    new URL("../app/publication-ui.tsx", import.meta.url),
    "utf8",
  );
  assert.match(map, /Math\.min\(window\.devicePixelRatio \|\| 1, 2\)/);
  assert.match(map, /Math\.min\(720,/);
  assert.match(map, /onPointerDown=\{handlePointerDown\}/);
  assert.match(map, /onPointerMove=\{handlePointerMove\}/);
  assert.match(map, /onPointerUp=\{handlePointerUp\}/);
  assert.match(map, /event\.key === "Enter" \|\| event\.key === " "/);
  assert.match(map, /TABLE_PAGE_SIZE = 25/);
  assert.match(map, /Show.*accessible correlation table/);
  assert.match(map, /aria-keyshortcuts=/);
  assert.match(chart, /publicationChartSegments/);
  assert.match(chart, /showDataTable/);
  assert.match(chart, /<caption>\{title\}\. Percentage returns/);
  assert.doesNotMatch(chart, /selector\(point\)[\s\S]*?return \[\]/);
});
