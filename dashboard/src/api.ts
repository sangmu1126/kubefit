import type {
  AnalysisReview,
  BenchmarkCampaignReview,
  BenchmarkReview,
  BenchmarkReviewRequest,
  CounterbalancedPairReview,
  EvaluationRequest,
  EvaluationResult,
} from "./types";

async function responseError(response: Response, fallback: string): Promise<Error> {
  let message = `${fallback} (${response.status})`;
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") {
      message = payload.detail;
    } else if (Array.isArray(payload.detail)) {
      const first = payload.detail[0] as { msg?: unknown } | undefined;
      if (typeof first?.msg === "string") message = `${fallback}: ${first.msg}`;
    }
  } catch {
    // Keep the stable HTTP fallback when the body is not JSON.
  }
  return new Error(message);
}

export async function evaluateResources(
  request: EvaluationRequest,
): Promise<EvaluationResult> {
  const response = await fetch("/v1/evaluations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw await responseError(response, "평가 요청 실패");
  }

  return (await response.json()) as EvaluationResult;
}

export async function reviewAnalysisArtifact(content: string): Promise<AnalysisReview> {
  const response = await fetch("/v1/analysis-reviews", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: content,
  });

  if (!response.ok) {
    throw await responseError(response, "analysis artifact 검증 실패");
  }

  return (await response.json()) as AnalysisReview;
}

export async function reviewBenchmarkArtifact(
  request: BenchmarkReviewRequest,
): Promise<BenchmarkReview> {
  const response = await fetch("/v1/benchmark-reviews", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw await responseError(response, "benchmark 결과 검증 실패");
  }

  return (await response.json()) as BenchmarkReview;
}

export async function fetchStoredBenchmarkReview(
  artifactId: string,
): Promise<BenchmarkReview> {
  const response = await fetch(
    `/v1/benchmark-results/${encodeURIComponent(artifactId)}/review`,
  );

  if (!response.ok) {
    throw await responseError(response, "저장된 benchmark 결과 검증 실패");
  }

  return (await response.json()) as BenchmarkReview;
}

export async function fetchStoredBenchmarkPairReview(
  artifactId: string,
): Promise<CounterbalancedPairReview> {
  const response = await fetch(
    `/v1/benchmark-pairs/${encodeURIComponent(artifactId)}/review`,
  );

  if (!response.ok) {
    throw await responseError(response, "저장된 benchmark pair 검증 실패");
  }

  return (await response.json()) as CounterbalancedPairReview;
}

export async function fetchStoredBenchmarkCampaignReview(
  artifactId: string,
): Promise<BenchmarkCampaignReview> {
  const response = await fetch(
    `/v1/benchmark-campaigns/${encodeURIComponent(artifactId)}/review`,
  );

  if (!response.ok) {
    throw await responseError(response, "저장된 benchmark campaign 검증 실패");
  }

  return (await response.json()) as BenchmarkCampaignReview;
}
