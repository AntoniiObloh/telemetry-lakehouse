# 0005. kind, not a cloud Kubernetes cluster

Status: Accepted, 2026-10-17.

## Context

Three jobs run on a schedule (generator-batch every 5 minutes, dbt-build at :15, publisher
at :30) and one runs all the time (generator-live). They need scheduling, retries, secrets
and resource limits. The budget is 5 USD a month (AWS Budgets alert at 80% and 100%), and
ThingsBoard already runs locally in docker compose.

## Decision

A local kind cluster, `lakehouse`, in Docker on the development machine. Manifests are
kustomize: `k8s/base` (the workloads, namespace `lakehouse-dev`, tags, security context,
requests and limits) and `k8s/overlays/kind` (`imagePullPolicy: Never`, the raw bucket).
Images are built locally and loaded with `kind load image-archive`; pods reach ThingsBoard
because its container joins the `kind` Docker network.

## Alternatives

- **EKS, GKE or AKS.** Real cloud Kubernetes, but a control plane alone costs more than the
  whole budget, plus nodes, a registry and networking.
- **k3d or minikube.** Equivalent for this use; kind was chosen for its simple image loading
  and because it is a plain Docker container.
- **No Kubernetes:** Windows Task Scheduler or cron for the scripts, or everything as
  Databricks jobs. Fewer parts, but no common way to declare limits, retries, secrets and
  concurrency, and the jobs that talk to local ThingsBoard cannot run in Databricks.

## Consequences

- No cloud cost for compute and no registry: an image change is build -> `kind load` ->
  next run (runbook, "Images").
- The jobs run only while the machine and Docker are on. A run missed by more than its
  `startingDeadlineSeconds` is skipped; generator-batch then has a hole in `raw/` until it
  is regenerated.
- `imagePullPolicy: Never` lives in the overlay only, so a cloud overlay (registry,
  `IfNotPresent`, cloud secrets) can reuse `k8s/base` unchanged.
- kubectl may only target `kind-*` contexts from an agent (the guard hook), so the same
  tooling cannot touch a real cluster by mistake.
