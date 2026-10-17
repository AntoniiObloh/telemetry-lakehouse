# Architecture decision records

One file per decision: context -> decision -> alternatives -> consequences. A decision that
changes gets a new record that supersedes the old one; old records are not rewritten.

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-auto-loader-for-bronze.md) | Auto Loader, not COPY INTO, for bronze | Accepted |
| [0002](0002-incremental-merge-with-3-day-window.md) | stg_telemetry: insert-only merge on event_id over the last 3 days of arrivals | Accepted |
| [0003](0003-srv-thingsboard-as-contract.md) | srv_thingsboard is the contract between dbt and the publisher | Accepted |
| [0004](0004-thingsboard-as-serving-layer.md) | ThingsBoard is the serving layer | Accepted |
| [0005](0005-kind-instead-of-cloud-kubernetes.md) | kind, not a cloud Kubernetes cluster | Accepted |
| [0006](0006-oidc-instead-of-keys-in-ci.md) | GitHub OIDC, not stored AWS keys, in CI | Accepted |
