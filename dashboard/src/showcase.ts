export const DECISION_JOURNEY_ID = "decision-journey";
export const VERIFIED_PAIR_ID = "benchmark-pair-dbc41864dd0dba9537ef228ebb340f60";

const repositoryRoot = "https://github.com/sangmu1126/kubefit";

export const decisionJourneyEvidence = {
  sources: {
    refinement: `${repositoryRoot}/blob/main/docs/devlog/0060-validation-informed-cpu-floor.md`,
    publication: `${repositoryRoot}/blob/main/docs/devlog/0061-live-pair-draft-publication.md`,
    replay: `${repositoryRoot}/blob/main/docs/devlog/0064-public-replayable-pair-demo.md`,
    draftPullRequest: `${repositoryRoot}/pull/23`,
  },
  observation: {
    requests: 100_501,
    errors: 0,
    usageCoveragePercent: 100,
    throttlingCoveragePercent: 100,
  },
  rejected: {
    cpuRequestMillicores: 10,
    cpuLimitMillicores: 20,
    steadyP99ChangePercent: 40.804,
  },
  refined: {
    cpuRequestMillicores: 20,
    cpuLimitMillicores: 40,
    memoryRequestMiB: 32,
    memoryLimitMiB: 48,
  },
  costProjection: {
    currentUsd: "73.000000",
    recommendedUsd: "1.396125",
    changePercent: "-98.088",
  },
} as const;
