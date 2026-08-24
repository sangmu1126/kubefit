import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { AnalysisReview, BenchmarkReview, EvaluationResult } from "./types";

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
  artifact_schema_version: 1,
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

const benchmarkBefore: BenchmarkReview["before"] = {
    schema_version: 1,
    profile_version: "kubefit-load-v1",
    proposal_id: "proposal-925669808e28e594baeeb442c3d447c8",
    variant: "before",
    dropped_iterations: 0,
    steady: { expected_iterations: 300, completed_iterations: 300, requests: 300, error_rate: 0, latency_p95_ms: 10.3, latency_p99_ms: 11.1 },
    spike: { expected_iterations: 750, completed_iterations: 750, requests: 750, error_rate: 0, latency_p95_ms: 8.1, latency_p99_ms: 8.8 },
    recovery: { expected_iterations: 300, completed_iterations: 300, requests: 300, error_rate: 0, latency_p95_ms: 10, latency_p99_ms: 11 },
    runtime: { cpu_throttling_p95_percent: 0, oom_killed_count: 0, restart_count: 0, traffic_spike_recovery_seconds: 5, traffic_spike_recovered: true },
    provenance: { run_started_at: "2026-08-21T00:00:00Z", run_finished_at: "2026-08-21T00:03:00Z", pods: ["api-before"], k6_summary_sha256: "a".repeat(64), k6_raw_sha256: "b".repeat(64), prometheus_rate_window_seconds: 30 },
    request_cost_usd: "73.000000",
};

const benchmarkReview: BenchmarkReview = {
  schema_version: 1,
  artifact_id: "benchmark-f84d0caf061d50a5d93bc03088eb0247",
  proposal_id: "proposal-925669808e28e594baeeb442c3d447c8",
  verification_level: "index_bound_replay",
  before: benchmarkBefore,
  after: {
    ...structuredClone(benchmarkBefore),
    variant: "after",
    steady: { ...benchmarkBefore.steady, latency_p95_ms: 10, latency_p99_ms: 10.6 },
    spike: { ...benchmarkBefore.spike, latency_p95_ms: 8.2, latency_p99_ms: 9.1 },
    request_cost_usd: "1.395760",
  },
  verdict: {
    status: "pass",
    checks: [{ code: "new_oom_killed", status: "pass", reason: "candidate run observed no OOMKilled events" }],
    failures: [],
    invalid_reasons: [],
    warnings: [],
    cost_change_percent: "-98.088",
  },
  checks: [
    { code: "index_identity", status: "pass", reason: "artifact identity checked" },
    { code: "selected_payload_hashes", status: "pass", reason: "selected payloads checked" },
    { code: "proposal_binding", status: "pass", reason: "proposal checked" },
    { code: "raw_evidence_binding", status: "pass", reason: "raw evidence digests checked" },
    { code: "verdict_replay", status: "pass", reason: "verdict replayed" },
  ],
  limitations: ["k6 raw bytes were not uploaded"],
};

function directoryFile(path: string, content = `{\"path\":\"${path}\"}`): File {
  const file = new File([content], path.split("/").at(-1)!, { type: "application/json" });
  Object.defineProperty(file, "webkitRelativePath", {
    value: `benchmark-result/${path}`,
  });
  return file;
}

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

  it("distinguishes a replayed schema v2 recommendation", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({
        ...artifactReview,
        artifact_schema_version: 2,
        verification_level: "recommendation_replayed",
        checks: [
          ...artifactReview.checks,
          { code: "recommendation_replay", status: "pass", reason: "recommendation replayed" },
        ],
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(<App />);

    await userEvent.upload(
      screen.getByLabelText("analysis artifact JSON"),
      new File(['{"schema_version":2}'], "analysis.json", { type: "application/json" }),
    );

    expect(await screen.findByText("RECOMMENDATION REPLAYED")).toBeInTheDocument();
    expect(screen.getByText("ANALYSIS ARTIFACT · SCHEMA 2")).toBeInTheDocument();
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

  it("loads compact indexed benchmark files and renders the replayed result", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(benchmarkReview), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    render(<App />);
    const files = [
      directoryFile("result.json", "result-content"),
      directoryFile("measurements/before.json", "before-content"),
      directoryFile("measurements/after.json", "after-content"),
      directoryFile("verdict.json", "verdict-content"),
    ];

    await userEvent.upload(screen.getByLabelText("benchmark result directory"), files);

    expect(await screen.findByText("PASS")).toBeInTheDocument();
    expect(screen.getByText("INDEX-BOUND REPLAY")).toBeInTheDocument();
    expect(screen.getByText("-98.1%")).toBeInTheDocument();
    expect(screen.getByText(/k6 raw bytes were not uploaded/)).toBeInTheDocument();
    expect(screen.getByText("candidate run observed no OOMKilled events")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      "/v1/benchmark-reviews",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          result_json: "result-content",
          before_json: "before-content",
          after_json: "after-content",
          verdict_json: "verdict-content",
        }),
      }),
    );
  });

  it("rejects an incomplete benchmark folder before sending it", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    render(<App />);

    await userEvent.upload(
      screen.getByLabelText("benchmark result directory"),
      [directoryFile("result.json")],
    );

    expect(screen.getByRole("alert")).toHaveTextContent("필수 파일이 없습니다");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
