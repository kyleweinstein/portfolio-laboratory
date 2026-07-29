export type ImportedHolding = { symbol: string; weight: number };

export type PortfolioCsvResult = {
  holdings: ImportedHolding[];
  importedRows: number;
  ignoredRows: number;
  mergedRows: number;
  basis: "Value" | "Weight";
};

const SYMBOL_PATTERN = /^[A-Z0-9.^=-]{1,15}$/;
const SYMBOL_HEADERS = new Set(["symbol", "ticker", "tickersymbol"]);
const VALUE_HEADERS = new Set(["value", "marketvalue", "currentvalue", "positionvalue"]);
const WEIGHT_HEADERS = new Set(["weight", "weightpercent", "portfolioweight", "allocation", "allocationpercent"]);

function parseCsvRows(text: string) {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;

  for (let index = 0; index < text.length; index++) {
    const character = text[index];
    if (quoted) {
      if (character === "\"" && text[index + 1] === "\"") {
        field += "\"";
        index++;
      } else if (character === "\"") {
        quoted = false;
      } else {
        field += character;
      }
      continue;
    }

    if (character === "\"") {
      quoted = true;
    } else if (character === ",") {
      row.push(field);
      field = "";
    } else if (character === "\n" || character === "\r") {
      if (character === "\r" && text[index + 1] === "\n") index++;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
    } else {
      field += character;
    }
  }

  if (quoted) throw new Error("The CSV contains an unclosed quoted field.");
  if (field.length || row.length) {
    row.push(field);
    rows.push(row);
  }
  return rows.filter(cells => cells.some(cell => cell.trim()));
}

function normalizeHeader(value: string) {
  return value.replace(/^\uFEFF/, "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "");
}

function numericValue(raw: string) {
  const trimmed = raw.trim();
  const negative = /^\(.*\)$/.test(trimmed);
  const cleaned = trimmed.replace(/[()$,%\s,]/g, "").replace(/\u2212/g, "-");
  const parsed = Number(cleaned);
  return negative ? -parsed : parsed;
}

function findHeader(headers: string[], aliases: Set<string>) {
  return headers.findIndex(header => aliases.has(header));
}

export function parsePortfolioCsv(text: string): PortfolioCsvResult {
  const rows = parseCsvRows(text);
  if (rows.length < 2) throw new Error("The CSV must contain a header row and at least one holding.");

  const headers = rows[0].map(normalizeHeader);
  const symbolIndex = findHeader(headers, SYMBOL_HEADERS);
  const valueIndex = findHeader(headers, VALUE_HEADERS);
  const weightIndex = findHeader(headers, WEIGHT_HEADERS);
  const amountIndex = valueIndex >= 0 ? valueIndex : weightIndex;
  const basis: PortfolioCsvResult["basis"] = valueIndex >= 0 ? "Value" : "Weight";

  if (symbolIndex < 0 || amountIndex < 0) {
    throw new Error("Include a Symbol column and either a Value or Weight column.");
  }

  const amounts = new Map<string, number>();
  const issues: string[] = [];
  let importedRows = 0;
  let ignoredRows = 0;

  rows.slice(1).forEach((cells, rowOffset) => {
    const rowNumber = rowOffset + 2;
    const rawSymbol = (cells[symbolIndex] || "").trim();
    const rawAmount = (cells[amountIndex] || "").trim();
    if (!rawSymbol && !rawAmount) return;

    const symbol = rawSymbol.toUpperCase();
    if (symbol === "CASH") {
      ignoredRows++;
      return;
    }
    if (!symbol) {
      issues.push(`row ${rowNumber}: missing Symbol`);
      return;
    }
    if (!SYMBOL_PATTERN.test(symbol)) {
      issues.push(`row ${rowNumber}: invalid Symbol "${rawSymbol}"`);
      return;
    }

    const amount = numericValue(rawAmount);
    if (!Number.isFinite(amount)) {
      issues.push(`row ${rowNumber}: invalid ${basis} for ${symbol}`);
      return;
    }
    if (amount < 0) {
      issues.push(`row ${rowNumber}: ${basis} cannot be negative for ${symbol}`);
      return;
    }
    if (amount === 0) {
      ignoredRows++;
      return;
    }

    amounts.set(symbol, (amounts.get(symbol) || 0) + amount);
    importedRows++;
  });

  if (issues.length) {
    const detail = issues.slice(0, 3).join("; ");
    throw new Error(`Fix ${issues.length} CSV row${issues.length === 1 ? "" : "s"}: ${detail}${issues.length > 3 ? "; …" : ""}`);
  }
  if (!amounts.size) throw new Error("No holdings with a positive Value or Weight were found.");
  if (amounts.size > 250) throw new Error("Import at most 250 unique holdings at a time.");

  const total = [...amounts.values()].reduce((sum, amount) => sum + amount, 0);
  const holdings = [...amounts.entries()].map(([symbol, amount]) => ({
    symbol,
    weight: +(amount / total * 100).toFixed(4),
  }));
  const roundedTotal = holdings.reduce((sum, holding) => sum + holding.weight, 0);
  const largestIndex = holdings.reduce(
    (largest, holding, index) => holding.weight > holdings[largest].weight ? index : largest,
    0,
  );
  holdings[largestIndex] = {
    ...holdings[largestIndex],
    weight: +(holdings[largestIndex].weight + 100 - roundedTotal).toFixed(4),
  };

  return {
    holdings,
    importedRows,
    ignoredRows,
    mergedRows: importedRows - holdings.length,
    basis,
  };
}
