/// <reference lib="webworker" />

import {
  analyzePortfolio,
  optimizePortfolio,
  type AnalysisInput,
  type AnalysisResult,
  type PackedSeries,
} from "./analytics";

export type AnalyticsWorkerRequest =
  | { type: "LOAD_DATA"; requestId: number; revision: number; series: PackedSeries[] }
  | { type: "ANALYZE"; requestId: number; revision: number; input: AnalysisInput }
  | { type: "OPTIMIZE"; requestId: number; revision: number; objective: "minvol" | "maxsharpe"; riskFreeRate: number }
  | { type: "CANCEL"; requestId: number; revision: number };

export type AnalyticsWorkerResponse =
  | { type: "PROGRESS"; requestId: number; revision: number; phase: "analyzing" | "optimizing"; message: string }
  | { type: "RESULT"; requestId: number; revision: number; kind: "loaded"; result: null }
  | { type: "RESULT"; requestId: number; revision: number; kind: "analysis"; result: AnalysisResult }
  | { type: "RESULT"; requestId: number; revision: number; kind: "optimization"; result: number[] }
  | { type: "ERROR"; requestId: number; revision: number; message: string };

const loadedSeries = new Map<string, PackedSeries>();
let latestAnalysis: AnalysisResult | null = null;
let cancelledRevision = -1;

function respond(message: AnalyticsWorkerResponse, transfer: Transferable[] = []) {
  self.postMessage(message, { transfer });
}

self.onmessage = (event: MessageEvent<AnalyticsWorkerRequest>) => {
  const message = event.data;
  if (message.type === "CANCEL") {
    cancelledRevision = Math.max(cancelledRevision, message.revision);
    return;
  }
  try {
    if (message.type === "LOAD_DATA") {
      for (const item of message.series) loadedSeries.set(item.symbol, item);
      respond({ type: "RESULT", requestId: message.requestId, revision: message.revision, kind: "loaded", result: null });
      return;
    }
    if (message.type === "ANALYZE") {
      respond({ type: "PROGRESS", requestId: message.requestId, revision: message.revision, phase: "analyzing", message: "Computing aligned returns and correlations" });
      if (message.revision <= cancelledRevision) return;
      latestAnalysis = analyzePortfolio(message.input, loadedSeries);
      if (message.revision <= cancelledRevision) return;
      const outgoing: AnalysisResult = {
        ...latestAnalysis,
        alignedDays: latestAnalysis.alignedDays.slice(),
        benchmarkReturns: latestAnalysis.benchmarkReturns.slice(),
        portfolioReturns: latestAnalysis.portfolioReturns.slice(),
        correlationPacked: latestAnalysis.correlationPacked.slice(),
        assetReturns: latestAnalysis.assetReturns.map(values => values.slice()),
      };
      const transfer: Transferable[] = [
        outgoing.alignedDays.buffer,
        outgoing.benchmarkReturns.buffer,
        outgoing.portfolioReturns.buffer,
        outgoing.correlationPacked.buffer,
        ...outgoing.assetReturns.map(values => values.buffer),
      ];
      respond({ type: "RESULT", requestId: message.requestId, revision: message.revision, kind: "analysis", result: outgoing }, transfer);
      return;
    }
    if (!latestAnalysis || latestAnalysis.revision !== message.revision) {
      throw new Error("Analyze the current portfolio before optimizing it.");
    }
    respond({ type: "PROGRESS", requestId: message.requestId, revision: message.revision, phase: "optimizing", message: "Searching the constrained allocation" });
    const result = optimizePortfolio(latestAnalysis, message.objective, message.riskFreeRate);
    respond({ type: "RESULT", requestId: message.requestId, revision: message.revision, kind: "optimization", result });
  } catch (caught) {
    respond({
      type: "ERROR",
      requestId: message.requestId,
      revision: message.revision,
      message: caught instanceof Error ? caught.message : "The analytical worker could not complete the request.",
    });
  }
};

export {};
