FROM node:24-alpine AS dashboard-builder

WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --ignore-scripts
COPY dashboard ./
RUN npm run build

FROM python:3.14-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build

COPY pyproject.toml README.md ./
COPY api ./api
COPY benchmarks ./benchmarks
COPY collector ./collector
COPY evaluator ./evaluator
COPY gitops ./gitops
COPY recommender ./recommender

RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KUBEFIT_DASHBOARD_DIRECTORY=/opt/kubefit/dashboard

RUN groupadd --system --gid 10001 kubefit \
    && useradd --system --uid 10001 --gid kubefit --no-create-home kubefit
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels \
        /wheels/kubefit-*.whl \
    && rm -rf /wheels
COPY --from=dashboard-builder --chown=10001:10001 /dashboard/dist /opt/kubefit/dashboard

USER 10001:10001
EXPOSE 8000

ENTRYPOINT ["uvicorn"]
CMD ["api.main:app", "--host", "0.0.0.0", "--port", "8000"]
