"use client";

import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
  type WheelEvent,
} from "react";
import type { PublishedCorrelationMap } from "./publication-server";
import {
  PUBLISHED_MAP_MAX_ZOOM,
  clampPublishedMapPan,
  publishedMapCellFromPointer,
  revealPublishedMapCell,
  zoomPublishedMapAt,
  type PublishedMapPan,
} from "./published-map-geometry";

type Props = { data: PublishedCorrelationMap };
type Selection = { row: number; column: number };
type DragState = {
  clientX: number;
  clientY: number;
  pan: PublishedMapPan;
  moved: boolean;
};

const TABLE_PAGE_SIZE = 25;

export default function PublishedDiversificationMap({ data }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const shellRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<DragState | null>(null);
  const instructionsId = useId();
  const symbolsId = useId();
  const tableId = useId();
  const [size, setSize] = useState(320);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState<PublishedMapPan>({ x: 0, y: 0 });
  const [selected, setSelected] = useState<Selection>({
    row: 0,
    column: Math.min(1, data.symbols.length - 1),
  });
  const [query, setQuery] = useState("");
  const [searchStatus, setSearchStatus] = useState("");
  const [tableOpen, setTableOpen] = useState(false);
  const [tablePage, setTablePage] = useState(0);

  useEffect(() => {
    const shell = shellRef.current;
    if (!shell) return;
    const update = () => {
      const nextSize = Math.max(240, Math.min(720, Math.floor(shell.clientWidth)));
      setSize(nextSize);
      setPan(current => clampPublishedMapPan(nextSize, zoom, current));
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(shell);
    return () => observer.disconnect();
  }, [zoom]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const count = data.symbols.length;
    if (!canvas || !count) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.floor(size * ratio);
    canvas.height = Math.floor(size * ratio);
    canvas.style.width = `${size}px`;
    canvas.style.height = `${size}px`;
    const context = canvas.getContext("2d");
    if (!context) return;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, size, size);
    context.fillStyle = "#f1efe9";
    context.fillRect(0, 0, size, size);

    const safePan = clampPublishedMapPan(size, zoom, pan);
    const cell = (size / count) * zoom;
    const firstColumn = clamp(Math.floor(-safePan.x / cell), 0, count - 1);
    const lastColumn = clamp(Math.ceil((size - safePan.x) / cell), 0, count);
    const firstRow = clamp(Math.floor(-safePan.y / cell), 0, count - 1);
    const lastRow = clamp(Math.ceil((size - safePan.y) / cell), 0, count);

    context.save();
    context.beginPath();
    context.rect(0, 0, size, size);
    context.clip();
    for (let row = firstRow; row < lastRow; row += 1) {
      for (let column = firstColumn; column < lastColumn; column += 1) {
        const correlation = correlationAt(data, row, column);
        const x = safePan.x + column * cell;
        const y = safePan.y + row * cell;
        context.fillStyle = row === column
          ? "#d8d5cc"
          : correlationColor(correlation);
        context.fillRect(x, y, Math.ceil(cell + 0.25), Math.ceil(cell + 0.25));
        if (cell >= 28) {
          context.fillStyle = Math.abs(correlation) > 0.62 ? "#fafaf7" : "#111111";
          context.font = "10px IBM Plex Mono, monospace";
          context.textAlign = "center";
          context.textBaseline = "middle";
          context.fillText(correlation.toFixed(2), x + cell / 2, y + cell / 2);
        }
      }
    }

    const rowY = safePan.y + selected.row * cell;
    const columnX = safePan.x + selected.column * cell;
    context.strokeStyle = "#111111";
    context.lineWidth = 1;
    context.setLineDash([4, 3]);
    context.strokeRect(safePan.x, rowY, count * cell, cell);
    context.strokeRect(columnX, safePan.y, cell, count * cell);
    context.setLineDash([]);
    context.strokeStyle = "#111111";
    context.lineWidth = Math.max(2, Math.min(4, cell / 4));
    context.strokeRect(columnX, rowY, cell, cell);
    context.strokeStyle = "#fafaf7";
    context.lineWidth = 1;
    context.strokeRect(
      columnX + 2,
      rowY + 2,
      Math.max(0, cell - 4),
      Math.max(0, cell - 4),
    );
    context.restore();
  }, [data, pan, selected, size, zoom]);

  const selectedCorrelation = useMemo(
    () => correlationAt(data, selected.row, selected.column),
    [data, selected],
  );
  const selectedRows = useMemo(
    () => data.symbols.map((symbol, index) => ({
      symbol,
      correlation: correlationAt(data, selected.row, index),
    })),
    [data, selected.row],
  );
  const pageCount = Math.max(1, Math.ceil(selectedRows.length / TABLE_PAGE_SIZE));
  const visibleRows = selectedRows.slice(
    tablePage * TABLE_PAGE_SIZE,
    tablePage * TABLE_PAGE_SIZE + TABLE_PAGE_SIZE,
  );

  if (!data.symbols.length) {
    return <div className="notice">Correlation data is unavailable.</div>;
  }

  function cellFromPointer(clientX: number, clientY: number): Selection | null {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    return publishedMapCellFromPointer({
      clientX,
      clientY,
      bounds: canvas.getBoundingClientRect(),
      size,
      count: data.symbols.length,
      zoom,
      pan,
    });
  }

  function selectCell(row: number, column: number) {
    const next = {
      row: clamp(row, 0, data.symbols.length - 1),
      column: clamp(column, 0, data.symbols.length - 1),
    };
    setSelected(next);
    setTablePage(0);
    setPan(current => revealPublishedMapCell(
      size,
      data.symbols.length,
      zoom,
      current,
      next.row,
      next.column,
    ));
  }

  function searchSymbol() {
    const normalized = query.trim().toUpperCase();
    const index = data.symbols.indexOf(normalized);
    if (index < 0) {
      setSearchStatus(normalized ? `${normalized} is not in this portfolio.` : "Enter a ticker to find.");
      return;
    }
    const column = selected.column === index && data.symbols.length > 1
      ? (index + 1) % data.symbols.length
      : selected.column;
    const nextZoom = Math.max(zoom, Math.min(PUBLISHED_MAP_MAX_ZOOM, data.symbols.length / 18));
    const cell = (size / data.symbols.length) * nextZoom;
    const nextPan = clampPublishedMapPan(size, nextZoom, {
      x: size / 2 - (column + 0.5) * cell,
      y: size / 2 - (index + 0.5) * cell,
    });
    setZoom(nextZoom);
    setPan(nextPan);
    setSelected({ row: index, column });
    setTablePage(0);
    setSearchStatus(`${normalized} is centered in the map.`);
  }

  function changeZoom(nextZoom: number, anchor = { x: size / 2, y: size / 2 }) {
    const next = zoomPublishedMapAt(size, zoom, pan, nextZoom, anchor);
    setZoom(next.zoom);
    setPan(next.pan);
  }

  function resetView() {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }

  function handleKeyDown(event: KeyboardEvent<HTMLCanvasElement>) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setTableOpen(true);
      setTablePage(0);
      return;
    }
    let row = selected.row;
    let column = selected.column;
    if (event.key === "ArrowUp") row -= 1;
    else if (event.key === "ArrowDown") row += 1;
    else if (event.key === "ArrowLeft") column -= 1;
    else if (event.key === "ArrowRight") column += 1;
    else if (event.key === "Home") { row = 0; column = 0; }
    else if (event.key === "End") {
      row = data.symbols.length - 1;
      column = data.symbols.length - 1;
    } else return;
    event.preventDefault();
    selectCell(row, column);
  }

  function handlePointerDown(event: PointerEvent<HTMLCanvasElement>) {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      clientX: event.clientX,
      clientY: event.clientY,
      pan,
      moved: false,
    };
  }

  function handlePointerMove(event: PointerEvent<HTMLCanvasElement>) {
    const drag = dragRef.current;
    const canvas = canvasRef.current;
    if (!drag || !canvas) return;
    const bounds = canvas.getBoundingClientRect();
    const deltaX = (event.clientX - drag.clientX) * (size / Math.max(1, bounds.width));
    const deltaY = (event.clientY - drag.clientY) * (size / Math.max(1, bounds.height));
    if (Math.abs(deltaX) + Math.abs(deltaY) > 4) drag.moved = true;
    if (drag.moved) {
      setPan(clampPublishedMapPan(size, zoom, {
        x: drag.pan.x + deltaX,
        y: drag.pan.y + deltaY,
      }));
    }
  }

  function handlePointerUp(event: PointerEvent<HTMLCanvasElement>) {
    const drag = dragRef.current;
    if (drag && !drag.moved) {
      const cell = cellFromPointer(event.clientX, event.clientY);
      if (cell) selectCell(cell.row, cell.column);
    }
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function handleWheel(event: WheelEvent<HTMLCanvasElement>) {
    event.preventDefault();
    const bounds = event.currentTarget.getBoundingClientRect();
    const anchor = {
      x: (event.clientX - bounds.left) * (size / Math.max(1, bounds.width)),
      y: (event.clientY - bounds.top) * (size / Math.max(1, bounds.height)),
    };
    changeZoom(zoom * (event.deltaY < 0 ? 1.2 : 1 / 1.2), anchor);
  }

  return <div className="published-map">
    <p className="chart-intro" id={instructionsId}>Click or tap a cell to select it. Drag to pan after zooming. Arrow keys move the selection; Home and End jump to the corners; Enter or Space opens the exact correlation table.</p>
    <div className="published-map-tools" aria-label="Correlation map controls">
      <label>Find holding<input value={query} list={symbolsId} onChange={event => setQuery(event.target.value.toUpperCase())} onKeyDown={event => { if (event.key === "Enter") searchSymbol(); }} placeholder="Ticker"/></label>
      <datalist id={symbolsId}>{data.symbols.map(symbol => <option value={symbol} key={symbol}/>)}</datalist>
      <button className="secondary compact" type="button" onClick={searchSymbol}>Find</button>
      <button className="secondary map-button" type="button" aria-label="Zoom out" disabled={zoom <= 1} onClick={() => changeZoom(zoom / 1.35)}>−</button>
      <button className="secondary map-button" type="button" aria-label="Zoom in" disabled={zoom >= PUBLISHED_MAP_MAX_ZOOM} onClick={() => changeZoom(zoom * 1.35)}>+</button>
      <button className="secondary compact" type="button" onClick={resetView}>Reset view</button>
      <span className="published-map-zoom" aria-live="polite">{zoom.toFixed(1)}× zoom</span>
    </div>
    {searchStatus && <div className="sr-only" role="status">{searchStatus}</div>}
    <div className="published-map-shell" ref={shellRef}>
      <canvas
        ref={canvasRef}
        role="img"
        tabIndex={0}
        aria-describedby={instructionsId}
        aria-keyshortcuts="ArrowUp ArrowDown ArrowLeft ArrowRight Home End Enter Space"
        aria-label={`Correlation map for ${data.symbols.length} holdings. Selected ${data.symbols[selected.row]} and ${data.symbols[selected.column]}, correlation ${selectedCorrelation.toFixed(2)}.`}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={() => { dragRef.current = null; }}
        onKeyDown={handleKeyDown}
        onWheel={handleWheel}
      />
    </div>
    <div className="published-map-legend" aria-label="Correlation color scale from negative one through neutral zero to positive one"><span>−1</span><i className="negative"/><span>0</span><i className="neutral"/><span>+1</span><i className="positive"/></div>
    <div className="published-pair-selection" aria-live="polite">
      <span>Selected pair</span>
      <strong>{data.symbols[selected.row]} / {data.symbols[selected.column]}</strong>
      <b>{selectedCorrelation.toFixed(2)} correlation</b>
    </div>
    <button className="link semantic-toggle" type="button" aria-expanded={tableOpen} aria-controls={tableId} onClick={() => setTableOpen(value => !value)}>{tableOpen ? "Hide" : "Show"} accessible correlation table</button>
    {tableOpen && <div className="semantic-table" id={tableId}>
      <div className="table-wrap"><table><caption>Correlations with {data.symbols[selected.row]}</caption><thead><tr><th scope="col">Holding</th><th scope="col">Correlation</th></tr></thead><tbody>{visibleRows.map(row => <tr key={row.symbol}><th scope="row">{row.symbol}</th><td>{row.correlation.toFixed(2)}</td></tr>)}</tbody></table></div>
      <div className="pagination"><button className="secondary compact" type="button" disabled={tablePage === 0} onClick={() => setTablePage(page => Math.max(0, page - 1))}>Previous</button><span>Page {tablePage + 1} of {pageCount}</span><button className="secondary compact" type="button" disabled={tablePage >= pageCount - 1} onClick={() => setTablePage(page => Math.min(pageCount - 1, page + 1))}>Next</button></div>
    </div>}
  </div>;
}

function correlationAt(data: PublishedCorrelationMap, left: number, right: number): number {
  const row = Math.min(left, right);
  const column = Math.max(left, right);
  const start = row * data.symbols.length - (row * (row - 1)) / 2;
  return data.packedCorrelations[start + column - row] ?? 0;
}

function correlationColor(correlation: number): string {
  const clamped = Math.max(-1, Math.min(1, correlation));
  if (clamped < 0) return mix("#f1efe9", "#ff3b00", Math.abs(clamped));
  return mix("#f1efe9", "#2e5cc8", clamped);
}

function mix(from: string, to: string, amount: number): string {
  const left = hex(from);
  const right = hex(to);
  return `rgb(${left.map((value, index) => Math.round(value + (right[index] - value) * amount)).join(",")})`;
}

function hex(value: string): number[] {
  return [1, 3, 5].map(index => Number.parseInt(value.slice(index, index + 2), 16));
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}
