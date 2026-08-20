import type { EvaluationRequest, EvaluationResult } from "./types";

export async function evaluateResources(
  request: EvaluationRequest,
): Promise<EvaluationResult> {
  const response = await fetch("/v1/evaluations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    let detail = `평가 요청 실패 (${response.status})`;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") detail = payload.detail;
    } catch {
      // Keep the stable HTTP fallback when the body is not JSON.
    }
    throw new Error(detail);
  }

  return (await response.json()) as EvaluationResult;
}
