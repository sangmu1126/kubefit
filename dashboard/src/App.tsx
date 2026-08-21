import { ChangeEvent, FormEvent, useState } from "react";
import { evaluateResources, reviewAnalysisArtifact } from "./api";
import { eligibleScenario, insufficientScenario } from "./scenarios";
import type {
  CheckStatus,
  AnalysisReview,
  EvaluationRequest,
  EvaluationResult,
  Resources,
  RiskLevel,
  DecimalValue,
} from "./types";

const MAX_ARTIFACT_BYTES = 1024 * 1024;

type NumericPath =
  | keyof EvaluationRequest["current"]
  | keyof EvaluationRequest["observed"]
  | "replica_count";

const resourceRows: Array<{
  label: string;
  key: keyof Resources;
  unit: string;
}> = [
  { label: "CPU request", key: "cpu_request_millicores", unit: "m" },
  { label: "CPU limit", key: "cpu_limit_millicores", unit: "m" },
  { label: "Memory request", key: "memory_request_mib", unit: "Mi" },
  { label: "Memory limit", key: "memory_limit_mib", unit: "Mi" },
];

function cloneScenario(scenario: EvaluationRequest): EvaluationRequest {
  return structuredClone(scenario);
}

function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("analysis artifact 파일을 읽을 수 없습니다."));
    reader.readAsText(file);
  });
}

function formatMoney(value: DecimalValue): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(Number(value));
}

function riskLabel(level: RiskLevel): string {
  return { low: "낮음", medium: "주의", high: "높음", unknown: "판단 불가" }[level];
}

function statusLabel(status: CheckStatus): string {
  return { pass: "통과", warning: "검토", block: "차단" }[status];
}

function NumberField({
  label,
  value,
  unit,
  min = 0,
  step = 1,
  required = true,
  onChange,
}: {
  label: string;
  value: number | undefined;
  unit?: string;
  min?: number;
  step?: number;
  required?: boolean;
  onChange: (value: number) => void;
}) {
  return (
    <label className="number-field">
      <span>{label}</span>
      <span className="input-shell">
        <input
          aria-label={label}
          type="number"
          min={min}
          step={step}
          value={value ?? ""}
          required={required}
          onChange={(event) => onChange(Number(event.target.value))}
        />
        {unit && <small>{unit}</small>}
      </span>
    </label>
  );
}

function ResourceComparison({ result }: { result: EvaluationResult }) {
  return (
    <section className="panel comparison-panel" aria-labelledby="resource-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">CAPACITY</p>
          <h2 id="resource-title">현재와 추천 리소스</h2>
        </div>
        <p>P95/P99 + 안전 여유 25%</p>
      </div>
      <div className="comparison-head" aria-hidden="true">
        <span />
        <span>현재</span>
        <span>추천</span>
      </div>
      <div className="resource-rows">
        {resourceRows.map((row) => {
          const current = result.current[row.key];
          const recommended = result.recommendation.recommended[row.key];
          const max = Math.max(current, recommended);
          return (
            <div className="resource-row" key={row.key}>
              <strong>{row.label}</strong>
              <div className="bar-cell">
                <span>{current.toLocaleString()}{row.unit}</span>
                <i style={{ width: `${(current / max) * 100}%` }} />
              </div>
              <div className="bar-cell recommended">
                <span>{recommended.toLocaleString()}{row.unit}</span>
                <i style={{ width: `${(recommended / max) * 100}%` }} />
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function DecisionPanel({ result }: { result: EvaluationResult }) {
  const eligible = result.patch_eligibility.status === "eligible";
  return (
    <section className={`decision ${eligible ? "eligible" : "blocked"}`}>
      <div className="decision-copy">
        <p className="eyebrow">GITOPS DECISION GATE</p>
        <h2>{eligible ? "패치 제안 가능" : "패치 제안 차단"}</h2>
        <p>
          {eligible
            ? "관측 근거와 안전성 검사를 통과했습니다. 다음 단계에서 YAML 변경안을 검토할 수 있습니다."
            : "비용 절감 예상값은 참고용입니다. 근거가 충분해질 때까지 YAML과 클러스터는 변경하지 않습니다."}
        </p>
      </div>
      <span className="decision-mark" aria-label={eligible ? "eligible" : "blocked"}>
        {eligible ? "✓" : "!"}
      </span>
    </section>
  );
}

function ArtifactContext({ review }: { review: AnalysisReview }) {
  const replayed = review.verification_level === "recommendation_replayed";
  return (
    <section className="artifact-context" aria-labelledby="artifact-title">
      <div>
        <p className="eyebrow">ANALYSIS ARTIFACT · SCHEMA {review.artifact_schema_version}</p>
        <h2 id="artifact-title">
          {review.target.namespace} / {review.target.deployment}
        </h2>
        <p>
          container <strong>{review.target.container}</strong> · created {new Date(review.workload_created_at).toLocaleString("ko-KR")}
        </p>
      </div>
      <div className="artifact-verification">
        <span>무결성 검사 {review.checks.length}/{review.checks.length}</span>
        <strong>{replayed ? "RECOMMENDATION REPLAYED" : "INTEGRITY ONLY"}</strong>
        <code title={review.workload_uid}>{review.workload_uid}</code>
      </div>
      <details>
        <summary>검증 범위와 한계</summary>
        <ul>
          {review.checks.map((check) => <li key={check.code}>✓ {check.reason}</li>)}
          {review.limitations.map((limitation) => <li className="limitation" key={limitation}>△ {limitation}</li>)}
        </ul>
      </details>
    </section>
  );
}

function Results({ result, review }: { result: EvaluationResult; review: AnalysisReview | null }) {
  return (
    <main className="results" aria-live="polite">
      {review && <ArtifactContext review={review} />}
      <DecisionPanel result={result} />
      <section className="metric-grid" aria-label="평가 요약">
        <article className="metric-card accent">
          <p>예상 request 비용 절감</p>
          <strong>{Number(result.cost.savings_percent).toFixed(1)}%</strong>
          <span>
            월 {formatMoney(result.cost.current.total_usd)} → {formatMoney(result.cost.recommended.total_usd)}
          </span>
        </article>
        <article className="metric-card">
          <p>OOM 위험</p>
          <strong className={`risk-${result.recommendation.risk.oom}`}>
            {riskLabel(result.recommendation.risk.oom)}
          </strong>
          <span>memory max 대비 limit 여유 기반</span>
        </article>
        <article className="metric-card">
          <p>CPU throttling 위험</p>
          <strong className={`risk-${result.recommendation.risk.cpu_throttling}`}>
            {riskLabel(result.recommendation.risk.cpu_throttling)}
          </strong>
          <span>throttled-period P95와 CPU headroom 기반</span>
        </article>
      </section>
      <ResourceComparison result={result} />
      <div className="evidence-grid">
        <section className="panel" aria-labelledby="checks-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">SAFETY CHECKS</p>
              <h2 id="checks-title">적용 전 판단</h2>
            </div>
          </div>
          <ul className="check-list">
            {result.patch_eligibility.checks.map((check) => (
              <li key={check.code}>
                <span className={`status-dot ${check.status}`} />
                <div>
                  <strong>{statusLabel(check.status)}</strong>
                  <p>{check.reason}</p>
                </div>
              </li>
            ))}
          </ul>
        </section>
        <section className="panel" aria-labelledby="evidence-title">
          <div className="section-heading">
            <div>
              <p className="eyebrow">WHY THIS VALUE</p>
              <h2 id="evidence-title">추천 근거</h2>
            </div>
          </div>
          <ol className="evidence-list">
            {result.recommendation.evidence.map((item) => <li key={item}>{item}</li>)}
          </ol>
        </section>
      </div>
      <details className="caveats">
        <summary>비용 추정의 전제와 한계</summary>
        <p>
          {result.cost.replica_count} replicas · {result.cost.assumptions.monthly_hours} hours · {result.cost.assumptions.price_source}
        </p>
        <ul>{result.cost.caveats.map((item) => <li key={item}>{item}</li>)}</ul>
      </details>
    </main>
  );
}

export default function App() {
  const [request, setRequest] = useState(() => cloneScenario(eligibleScenario));
  const [result, setResult] = useState<EvaluationResult | null>(null);
  const [artifactReview, setArtifactReview] = useState<AnalysisReview | null>(null);
  const [loading, setLoading] = useState(false);
  const [artifactLoading, setArtifactLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const setCurrent = (key: keyof EvaluationRequest["current"], value: number) => {
    setRequest((previous) => ({
      ...previous,
      current: { ...previous.current, [key]: value },
    }));
  };

  const setObserved = (key: keyof EvaluationRequest["observed"], value: number) => {
    setRequest((previous) => ({
      ...previous,
      observed: { ...previous.observed, [key]: value },
    }));
  };

  const useScenario = (scenario: EvaluationRequest) => {
    setRequest(cloneScenario(scenario));
    setResult(null);
    setArtifactReview(null);
    setError(null);
  };

  const loadArtifact = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setResult(null);
    setArtifactReview(null);
    setError(null);
    if (file.size > MAX_ARTIFACT_BYTES) {
      setError("analysis artifact는 1 MiB 이하여야 합니다.");
      return;
    }
    setArtifactLoading(true);
    try {
      const review = await reviewAnalysisArtifact(await readFile(file));
      setArtifactReview(review);
      setResult(review.evaluation);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "analysis artifact 검증에 실패했습니다.");
    } finally {
      setArtifactLoading(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setArtifactReview(null);
    setError(null);
    try {
      setResult(await evaluateResources(request));
    } catch (reason) {
      setResult(null);
      setError(reason instanceof Error ? reason.message : "평가 요청에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  };

  const fields: Array<{
    group: "current" | "observed";
    key: NumericPath;
    label: string;
    unit?: string;
    step?: number;
  }> = [
    { group: "current", key: "cpu_request_millicores", label: "CPU request", unit: "m" },
    { group: "current", key: "cpu_limit_millicores", label: "CPU limit", unit: "m" },
    { group: "current", key: "memory_request_mib", label: "Memory request", unit: "Mi" },
    { group: "current", key: "memory_limit_mib", label: "Memory limit", unit: "Mi" },
    { group: "observed", key: "cpu_p95_millicores", label: "CPU P95", unit: "m" },
    { group: "observed", key: "cpu_max_millicores", label: "CPU max", unit: "m" },
    { group: "observed", key: "memory_p99_mib", label: "Memory P99", unit: "Mi" },
    { group: "observed", key: "memory_max_mib", label: "Memory max", unit: "Mi" },
    { group: "observed", key: "sample_count", label: "Usage samples" },
    { group: "observed", key: "observation_coverage", label: "Usage coverage", unit: "0–1", step: 0.01 },
    { group: "observed", key: "cpu_throttling_p95_percent", label: "Throttling P95", unit: "%", step: 0.1 },
    { group: "observed", key: "cpu_throttling_sample_count", label: "Throttle samples" },
    { group: "observed", key: "restart_count", label: "Restarts" },
    { group: "observed", key: "oom_killed_count", label: "OOMKilled" },
  ];

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="KubeFit 홈">
          <span>K</span>KubeFit
        </a>
        <div className="principle"><i /> cluster mutation: off</div>
      </header>
      <div className="hero" id="top">
        <div>
          <p className="eyebrow">EXPLAINABLE · GITOPS-FIRST</p>
          <h1>줄여도 되는 이유를<br />먼저 보여줍니다.</h1>
          <p className="hero-copy">
            실제 관측값으로 requests/limits를 계산하고, 비용과 위험을 함께 검토한 뒤에만 변경안을 제안합니다.
          </p>
        </div>
        <div className="hero-rule">
          <span>01</span><p>Measure</p><span>02</span><p>Explain</p><span>03</span><p>Review</p>
        </div>
      </div>
      <div className="workspace">
        <aside className="input-column">
          <form className="panel input-panel" onSubmit={submit}>
            <div className="section-heading">
              <div>
                <p className="eyebrow">SCENARIO INPUT</p>
                <h2>워크로드 관측값</h2>
              </div>
            </div>
            <label className="artifact-loader">
              <span className="artifact-loader-icon">↥</span>
              <span>
                <strong>{artifactLoading ? "artifact 검증 중…" : "analysis.json 불러오기"}</strong>
                <small>kubefit analyze 출력 · 최대 1 MiB</small>
              </span>
              <input
                aria-label="analysis artifact JSON"
                type="file"
                accept="application/json,.json"
                disabled={artifactLoading}
                onChange={loadArtifact}
              />
            </label>
            <div className="input-divider"><span>또는 예제 입력</span></div>
            <div className="scenario-switch" aria-label="예제 시나리오">
              <button type="button" onClick={() => useScenario(eligibleScenario)}>검증 가능</button>
              <button type="button" onClick={() => useScenario(insufficientScenario)}>근거 부족</button>
            </div>
            <fieldset>
              <legend>현재 설정</legend>
              <div className="field-grid">
                {fields.filter(({ group }) => group === "current").map((field) => (
                  <NumberField
                    key={field.key}
                    label={field.label}
                    value={request.current[field.key as keyof EvaluationRequest["current"]]}
                    unit={field.unit}
                    step={field.step}
                    min={1}
                    onChange={(value) => setCurrent(field.key as keyof EvaluationRequest["current"], value)}
                  />
                ))}
              </div>
            </fieldset>
            <fieldset>
              <legend>Prometheus · Kubernetes 근거</legend>
              <div className="field-grid">
                {fields.filter(({ group }) => group === "observed").map((field) => (
                  <NumberField
                    key={field.key}
                    label={field.label}
                    value={request.observed[field.key as keyof EvaluationRequest["observed"]] as number | undefined}
                    unit={field.unit}
                    step={field.step}
                    required={field.key === "cpu_p95_millicores" || field.key === "memory_p99_mib"}
                    onChange={(value) => setObserved(field.key as keyof EvaluationRequest["observed"], value)}
                  />
                ))}
              </div>
            </fieldset>
            <button className="evaluate-button" type="submit" disabled={loading}>
              {loading ? "평가 중…" : "추천과 위험 계산"}<span>→</span>
            </button>
            {error && <p className="error" role="alert">{error}</p>}
          </form>
        </aside>
        {result ? <Results result={result} review={artifactReview} /> : (
          <main className="empty-state">
            <span>↳</span>
            <h2>아직 계산하지 않았습니다.</h2>
            <p>샘플을 선택하거나 관측값을 조정한 뒤 평가를 실행하세요. 결과는 기존 KubeFit API가 계산합니다.</p>
          </main>
        )}
      </div>
      <footer>OPEN SOURCE · HUMAN-APPROVED OPTIMIZATION</footer>
    </div>
  );
}
