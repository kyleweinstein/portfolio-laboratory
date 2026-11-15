/// <reference lib="webworker" />
import { simulatePortfolio, type SimulationSettings } from "./monte-carlo";
self.onmessage = (event: MessageEvent<{ returns: Float64Array; settings: SimulationSettings }>) => {
  try {
    const result = simulatePortfolio(event.data.returns, event.data.settings, percent => self.postMessage({ type: "progress", percent }));
    self.postMessage({ type: "result", result });
  } catch (error) {
    self.postMessage({ type: "error", message: error instanceof Error ? error.message : "Simulation failed." });
  }
};
export {};
