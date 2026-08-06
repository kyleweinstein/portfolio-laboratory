"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent, type WheelEvent } from "react";
import { correlationAt, type PairInsight } from "./analytics";

type Selection = { row: number; column: number };
type Props = {
  symbols: string[];
  correlation: Float32Array;
  clusterOrder: number[];
  pairs: PairInsight[];
  onCompare: (left: number, right: number) => void;
  focusedPair?: PairInsight | null;
};

const clamp = (value: number, minimum: number, maximum: number) => Math.min(maximum, Math.max(minimum, value));
const number = (value: number) => Number.isFinite(value) ? value.toFixed(2) : "—";
const percent = (value: number) => Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "—";

function cellColor(value: number, diagonal: boolean) {
  if (!Number.isFinite(value)) return "#D8D5CC";
  if (diagonal) return "#2A2A28";
  const strength = .08 + Math.abs(value) * .82;
  return value < 0 ? `rgba(255,59,0,${strength})` : `rgba(46,92,200,${strength})`;
}

export default function DiversificationMap({ symbols, correlation, clusterOrder, pairs, onCompare, focusedPair }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const drawRef = useRef<() => void>(() => undefined);
  const frameRef = useRef<number | null>(null);
  const hoverRef = useRef<Selection | null>(null);
  const dragRef = useRef<{ x: number; y: number; panX: number; panY: number; moved: boolean } | null>(null);
  const initialZoom = focusedPair ? Math.max(1, Math.min(16, symbols.length / 18)) : 1;
  const initialRow = focusedPair ? clusterOrder.indexOf(focusedPair.a) : 0;
  const initialColumn = focusedPair ? clusterOrder.indexOf(focusedPair.b) : Math.min(1, symbols.length - 1);
  const initialCell = (640 - 80) / Math.max(symbols.length, 1) * initialZoom;
  const [size, setSize] = useState(640);
  const [mode, setMode] = useState<"clustered" | "portfolio">("clustered");
  const [zoom, setZoom] = useState(initialZoom);
  const [pan, setPan] = useState(focusedPair ? {
    x: 640 / 2 - 72 - (initialColumn + .5) * initialCell,
    y: 640 / 2 - 72 - (initialRow + .5) * initialCell,
  } : { x: 0, y: 0 });
  const [selection, setSelection] = useState<Selection>(focusedPair
    ? { row: focusedPair.a, column: focusedPair.b }
    : { row: 0, column: Math.min(1, symbols.length - 1) });
  const [search, setSearch] = useState("");
  const [tableOpen, setTableOpen] = useState(false);
  const [tablePage, setTablePage] = useState(0);
  const order = mode === "clustered" ? clusterOrder : symbols.map((_, index) => index);
  const pairMap = useMemo(() => new Map(pairs.map(pair => [`${Math.min(pair.a, pair.b)}:${Math.max(pair.a, pair.b)}`, pair])), [pairs]);
  const selectedPair = selection.row === selection.column ? null : pairMap.get(`${Math.min(selection.row, selection.column)}:${Math.max(selection.row, selection.column)}`) || null;
  const selectedCorrelation = correlationAt(correlation, symbols.length, selection.row, selection.column);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const observer = new ResizeObserver(entries => {
      const width = entries[0]?.contentRect.width || 640;
      setSize(Math.round(clamp(width, 280, 720)));
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !symbols.length) return;
    const context = canvas.getContext("2d");
    if (!context) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(size * ratio);
    canvas.height = Math.round(size * ratio);
    canvas.style.width = `${size}px`;
    canvas.style.height = `${size}px`;

    const draw = () => {
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, size, size);
      context.fillStyle = "#FAFAF7";
      context.fillRect(0, 0, size, size);
      const initialCell = (size - 34) / Math.max(symbols.length, 1);
      const cell = initialCell * zoom;
      const margin = cell >= 8 ? 72 : 28;
      const plotSize = size - margin - 8;
      const effectiveCell = plotSize / Math.max(symbols.length, 1) * zoom;
      const originX = margin + pan.x;
      const originY = margin + pan.y;
      const group = effectiveCell < 1 ? Math.ceil(1 / Math.max(effectiveCell, .01)) : 1;

      context.save();
      context.beginPath();
      context.rect(margin, margin, plotSize, plotSize);
      context.clip();
      for (let displayRow = 0; displayRow < symbols.length; displayRow += group) {
        for (let displayColumn = 0; displayColumn < symbols.length; displayColumn += group) {
          let total = 0;
          let count = 0;
          for (let rowOffset = 0; rowOffset < group && displayRow + rowOffset < symbols.length; rowOffset++) {
            for (let columnOffset = 0; columnOffset < group && displayColumn + columnOffset < symbols.length; columnOffset++) {
              const row = order[displayRow + rowOffset];
              const column = order[displayColumn + columnOffset];
              const value = correlationAt(correlation, symbols.length, row, column);
              if (Number.isFinite(value)) { total += value; count++; }
            }
          }
          const value = count ? total / count : NaN;
          const x = originX + displayColumn * effectiveCell;
          const y = originY + displayRow * effectiveCell;
          const width = Math.max(1, Math.ceil(group * effectiveCell + .2));
          context.fillStyle = cellColor(value, group === 1 && displayRow === displayColumn);
          context.fillRect(x, y, width, width);
          if (group === 1 && effectiveCell >= 28 && x >= margin && y >= margin && x < size && y < size) {
            context.fillStyle = Math.abs(value) > .58 ? "#FAFAF7" : "#111111";
            context.font = "10px IBM Plex Mono, monospace";
            context.textAlign = "center";
            context.textBaseline = "middle";
            context.fillText(number(value), x + effectiveCell / 2, y + effectiveCell / 2);
          }
        }
      }

      const highlighted = hoverRef.current || selection;
      const highlightedRow = order.indexOf(highlighted.row);
      const highlightedColumn = order.indexOf(highlighted.column);
      context.strokeStyle = "#111111";
      context.lineWidth = 2;
      context.setLineDash([4, 3]);
      context.strokeRect(originX, originY + highlightedRow * effectiveCell, symbols.length * effectiveCell, effectiveCell);
      context.strokeRect(originX + highlightedColumn * effectiveCell, originY, effectiveCell, symbols.length * effectiveCell);
      context.setLineDash([]);
      context.strokeStyle = "#FF3B00";
      context.lineWidth = 3;
      context.strokeRect(originX + highlightedColumn * effectiveCell, originY + highlightedRow * effectiveCell, effectiveCell, effectiveCell);
      context.restore();

      if (effectiveCell >= 8) {
        const step = effectiveCell >= 28 ? 1 : Math.max(1, Math.ceil(18 / effectiveCell));
        context.fillStyle = "#4A4A46";
        context.font = `${effectiveCell >= 28 ? 10 : 9}px IBM Plex Mono, monospace`;
        context.textBaseline = "middle";
        for (let display = 0; display < symbols.length; display += step) {
          const coordinate = originY + display * effectiveCell + effectiveCell / 2;
          if (coordinate < margin || coordinate > size - 4) continue;
          context.textAlign = "right";
          context.fillText(symbols[order[display]], margin - 6, coordinate);
          const x = originX + display * effectiveCell + effectiveCell / 2;
          if (x < margin || x > size - 4) continue;
          context.save();
          context.translate(x, margin - 6);
          context.rotate(-Math.PI / 2);
          context.textAlign = "left";
          context.fillText(symbols[order[display]], 0, 0);
          context.restore();
        }
      }
      context.strokeStyle = "#111111";
      context.lineWidth = 1;
      context.strokeRect(margin, margin, plotSize, plotSize);
    };
    drawRef.current = draw;
    draw();
  }, [correlation, mode, order, pan, selection, size, symbols, zoom]);

  const requestDraw = () => {
    if (frameRef.current !== null) return;
    frameRef.current = requestAnimationFrame(() => {
      frameRef.current = null;
      drawRef.current();
    });
  };
  const cellFromPointer = (clientX: number, clientY: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    const scale = size / rect.width;
    const x = (clientX - rect.left) * scale;
    const y = (clientY - rect.top) * scale;
    const initialCell = (size - 34) / Math.max(symbols.length, 1);
    const margin = initialCell * zoom >= 8 ? 72 : 28;
    const effectiveCell = (size - margin - 8) / Math.max(symbols.length, 1) * zoom;
    const displayColumn = Math.floor((x - margin - pan.x) / effectiveCell);
    const displayRow = Math.floor((y - margin - pan.y) / effectiveCell);
    if (displayRow < 0 || displayColumn < 0 || displayRow >= symbols.length || displayColumn >= symbols.length) return null;
    return { row: order[displayRow], column: order[displayColumn] };
  };
  const focusSymbol = (symbol: string) => {
    const index = symbols.findIndex(value => value === symbol.trim().toUpperCase());
    if (index < 0) return;
    const display = order.indexOf(index);
    const nextZoom = Math.max(zoom, Math.min(16, symbols.length / 18));
    const margin = 72;
    const cell = (size - margin - 8) / symbols.length * nextZoom;
    setZoom(nextZoom);
    setPan({ x: size / 2 - margin - (display + .5) * cell, y: size / 2 - margin - (display + .5) * cell });
    setSelection({ row: index, column: index });
    setTablePage(0);
  };
  const reset = () => { setZoom(1); setPan({ x: 0, y: 0 }); };
  const onKeyDown = (event: KeyboardEvent<HTMLCanvasElement>) => {
    let row = order.indexOf(selection.row);
    let column = order.indexOf(selection.column);
    if (event.key === "ArrowUp") row--;
    else if (event.key === "ArrowDown") row++;
    else if (event.key === "ArrowLeft") column--;
    else if (event.key === "ArrowRight") column++;
    else if (event.key === "Home") { row = 0; column = 0; }
    else if (event.key === "End") { row = symbols.length - 1; column = symbols.length - 1; }
    else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setTableOpen(true);
      return;
    } else return;
    event.preventDefault();
    setSelection({ row: order[clamp(row, 0, symbols.length - 1)], column: order[clamp(column, 0, symbols.length - 1)] });
  };
  const onPointerDown = (event: PointerEvent<HTMLCanvasElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { x: event.clientX, y: event.clientY, panX: pan.x, panY: pan.y, moved: false };
  };
  const onPointerMove = (event: PointerEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    if (drag) {
      const deltaX = event.clientX - drag.x;
      const deltaY = event.clientY - drag.y;
      if (Math.abs(deltaX) + Math.abs(deltaY) > 4) drag.moved = true;
      if (drag.moved) setPan({ x: drag.panX + deltaX, y: drag.panY + deltaY });
      return;
    }
    hoverRef.current = cellFromPointer(event.clientX, event.clientY);
    requestDraw();
  };
  const onPointerUp = (event: PointerEvent<HTMLCanvasElement>) => {
    const drag = dragRef.current;
    if (drag && !drag.moved) {
      const cell = cellFromPointer(event.clientX, event.clientY);
      if (cell) { setSelection(cell); setTablePage(0); }
    }
    dragRef.current = null;
  };
  const onWheel = (event: WheelEvent<HTMLCanvasElement>) => {
    event.preventDefault();
    setZoom(value => clamp(value * (event.deltaY < 0 ? 1.2 : 1 / 1.2), 1, 16));
  };

  const selectedRows = symbols.map((symbol, index) => ({ symbol, index, value: correlationAt(correlation, symbols.length, selection.row, index) }))
    .sort((left, right) => left.value - right.value);
  const pageCount = Math.max(1, Math.ceil(selectedRows.length / 25));
  const visibleRows = selectedRows.slice(tablePage * 25, tablePage * 25 + 25);

  return <section className="card diversification-card">
    <div className="section-title">
      <div><span className="eyebrow">Diversification map</span><h2>Correlation explorer</h2></div>
      <span className="pill">{symbols.length} holdings · canvas</span>
    </div>
    <p className="chart-intro">One adaptive view moves from a clustered overview to exact pair values as you zoom. Orange is negative, blue is positive, and the dark diagonal is each holding against itself.</p>
    <div className="map-toolbar" aria-label="Diversification map controls">
      <label>Order<select value={mode} onChange={event => { setMode(event.target.value as typeof mode); reset(); }}><option value="clustered">Clustered</option><option value="portfolio">Portfolio order</option></select></label>
      <label>Find holding<input value={search} list="map-symbols" onChange={event => setSearch(event.target.value.toUpperCase())} onKeyDown={event => event.key === "Enter" && focusSymbol(search)} placeholder="Ticker"/></label>
      <datalist id="map-symbols">{symbols.map(symbol => <option value={symbol} key={symbol}/>)}</datalist>
      <button className="secondary compact" onClick={() => focusSymbol(search)} disabled={!symbols.includes(search.trim().toUpperCase())}>Find</button>
      <button className="secondary map-button" aria-label="Zoom out" onClick={() => setZoom(value => clamp(value / 1.35, 1, 16))}>−</button>
      <button className="secondary map-button" aria-label="Zoom in" onClick={() => setZoom(value => clamp(value * 1.35, 1, 16))}>+</button>
      <button className="secondary compact" onClick={reset}>Reset</button>
    </div>
    <div className="map-shell" ref={hostRef}>
      <canvas
        ref={canvasRef}
        role="grid"
        tabIndex={0}
        aria-label={`Correlation matrix for ${symbols.length} holdings. Use arrow keys to move and Enter to inspect.`}
        onKeyDown={onKeyDown}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerLeave={() => { hoverRef.current = null; requestDraw(); }}
        onPointerUp={onPointerUp}
        onPointerCancel={() => { dragRef.current = null; }}
        onWheel={onWheel}
      />
    </div>
    <div className="map-legend" aria-label="Correlation color legend"><span>−1</span><i className="negative"/><i className="neutral"/><i className="positive"/><span>+1</span><em>Missing data</em><i className="missing"/></div>
    <div className="pair-inspector" aria-live="polite">
      <div><span>Selected pair</span><strong>{symbols[selection.row]} / {symbols[selection.column]}</strong></div>
      <div><span>Correlation</span><strong>{number(selectedCorrelation)}</strong></div>
      <div><span>Spread volatility</span><strong>{selectedPair ? percent(selectedPair.spreadVolatility) : "—"}</strong></div>
      <div><span>Rebalance potential</span><strong>{selectedPair ? percent(selectedPair.rebalancePotential) : "—"}</strong></div>
      <button className="link" disabled={!selectedPair} onClick={() => selectedPair && onCompare(selectedPair.a, selectedPair.b)}>Compare directions ↑</button>
    </div>
    <button className="link semantic-toggle" aria-expanded={tableOpen} onClick={() => setTableOpen(value => !value)}>{tableOpen ? "Hide" : "Show"} accessible correlation table</button>
    {tableOpen && <div className="semantic-table">
      <div className="table-wrap"><table><caption>Correlations with {symbols[selection.row]}</caption><thead><tr><th>Holding</th><th>Correlation</th></tr></thead><tbody>{visibleRows.map(row => <tr key={row.symbol}><td>{row.symbol}</td><td>{number(row.value)}</td></tr>)}</tbody></table></div>
      <div className="pagination"><button className="secondary compact" disabled={tablePage === 0} onClick={() => setTablePage(page => page - 1)}>Previous</button><span>Page {tablePage + 1} of {pageCount}</span><button className="secondary compact" disabled={tablePage >= pageCount - 1} onClick={() => setTablePage(page => page + 1)}>Next</button></div>
    </div>}
    <p className="note">Correlation can change abruptly and does not capture tail dependence. At overview scale, multiple cells are area-averaged into screen pixels; zoom or search to inspect exact values.</p>
  </section>;
}
