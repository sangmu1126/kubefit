import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { AnalysisReview, EvaluationResult } from "./types";

const result: EvaluationResult = {
  current: {
    cpu_request_millicores: 1000,
    cpu_limit_millicores: 2000,
    memory_request_mib: 2048,
    memory_limit_mib: 4096,
  },
  recommendation: {
    recommended: {
      cpu_request_millicores: 290,
      cpu_limit_millicores: 580,
      memory_request_mib: 896,
      memory_limit_mib: 1344,
    },
    cpu_request_change_percent: -71,
    memory_request_change_percent: -56.3,
    readiness: { status: "ready", reasons: [] },
    risk: {
      oom: "low",
      cpu_throttling: "low",
      reasons: ["safe headroom"],
    },
    evidence: ["CPU request uses 7-day P95 plus 25% safety margin"],
  },
  cost: {
    assumptions: {
      currency: "USD",
      cpu_core_hour_usd: 0.04,
      memory_gib_hour_usd: 0.005,
      monthly_hours: 730,
      price_source: "example://local-model",
    },
    replica_count: 2,
    basis: "resource_requests",
    current: { cpu_usd: "58.400000", memory_usd: "14.600000", total_usd: "73.000000" },
    recommended: { cpu_usd: "16.936000", memory_usd: "6.387500", total_usd: "23.323500" },
    monthly_delta_usd: "-49.676500",
    savings_percent: "68.1",
    caveats: ["request cost is a projection"],
  },
  patch_eligibility: {
    status: "eligible",
    checks: [
      { code: "recommendation_readiness", status: "pass", reason: "evidence is sufficient" },
    ],
    blocking_reasons: [],
    warnings: [],
  },
};

const artifactReview: AnalysisReview = {
  schema_version: 1,
  verification_level: "integrity_only",
  target: { namespace: "payments", deployment: "checkout-api", container: "api" },
  workload_uid: "deployment-uid-1234",
  workload_created_at: "2026-08-21T00:00:00Z",
  evaluation: result,
  checks: [
    { code: "cost_comparison", status: "pass", reason: "cost comparison was recomputed" },
  ],
  limitations: ["schema v1 does not retain raw observed usage"],
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("KubeFit dashboard", () => {
  it("renders an explanation-first empty state", () => {
    render(<App />);
    expect(screen.getByText("아직 계산하지 않았습니다.")).toBeInTheDocument();
    expect(screen.getByLabelText("CPU P95")).toHaveValue(230);
  });

  it("submits the scenario to the evaluation API and renders its decision", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(result), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: /추천과 위험 계산/ }));

    expect(await screen.findByText("패치 제안 가능")).toBeInTheDocument();
    expect(screen.getByText("68.1%")).toBeInTheDocument();
    expect(screen.getByText("CPU request uses 7-day P95 plus 25% safety margin")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/evaluations",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("loads the insufficient-evidence scenario without pretending it is evaluated", async () => {
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "근거 부족" }));

    expect(screen.getByLabelText("Usage samples")).toHaveValue(null);
    expect(screen.getByText("아직 계산하지 않았습니다.")).toBeInTheDocument();
  });

  it("shows a stable API error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("unavailable", { status: 503 }));
    render(<App />);

    await userEvent.click(screen.getByRole("button", { name: /추천과 위험 계산/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("평가 요청 실패 (503)");
  });

  it("loads a backend-validated analysis artifact into the same review surface", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(artifactReview), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(<App />);
    const file = new File(['{"schema_version":1}'], "analysis.json", {
      type: "application/json",
    });

    await userEvent.upload(screen.getByLabelText("analysis artifact JSON"), file);

    expect(await screen.findByText("payments / checkout-api")).toBeInTheDocument();
    expect(screen.getByText("INTEGRITY ONLY")).toBeInTheDocument();
    expect(screen.getByText("패치 제안 가능")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/analysis-reviews",
      expect.objectContaining({ body: '{"schema_version":1}' }),
    );
  });

  it("rejects an oversized artifact before sending it", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    render(<App />);
    const file = new File([new Uint8Array(1024 * 1024 + 1)], "large.json", {
      type: "application/json",
    });

    await userEvent.upload(screen.getByLabelText("analysis artifact JSON"), file);

    expect(screen.getByRole("alert")).toHaveTextContent("1 MiB 이하여야 합니다");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
