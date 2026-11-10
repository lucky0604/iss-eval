# EvalScope service on Kubernetes

Minimal single-node deployment: one Pod runs the web process and the RQ worker
side by side, backed by a Redis instance with AOF persistence. It is a
single-writer topology, not a highly available one.

This directory has been statically validated only. Nothing here has been applied
to a cluster, and no result in the repository claims otherwise.

## What the manifests describe

| Object | Purpose |
| --- | --- |
| `namespace.yaml` | `evalscope` namespace. |
| `configmap.yaml` | Shared queue/cache/outputs configuration plus the `evalscope-data` (100Gi) and `evalscope-redis-data` (1Gi) claims. |
| `redis.yaml` | Redis 7.2 service and single-replica workload, AOF `everysec` on the Redis claim. |
| `deployment.yaml` | One `evalscope` Pod with the `web` and `worker` containers on the same image, plus the ClusterIP service on port 9000. |
| `kustomization.yaml` | Applies the four files in order. |

Both containers read the same ConfigMap, so `EVALSCOPE_QUEUE_BACKEND`,
`EVALSCOPE_REDIS_URL` and `EVALSCOPE_REDIS_QUEUE` cannot drift between producer
and consumer. The web process serves `GET /health` (liveness) and `GET /ready`
(readiness); the worker has no HTTP server, so its liveness probe asks Redis
whether its `rq:worker:*` registration key is still alive.

## Before applying

1. Build and push the image with an immutable tag or digest:

   ```bash
   docker build -f docker/Dockerfile -t <registry>/evalscope:<version-or-commit> .
   docker push <registry>/evalscope:<version-or-commit>
   ```

2. Replace `evalscope:0.0.0_dev` in `deployment.yaml` with that tag or digest.
   The placeholder is deliberately a development tag: a floating `latest` tag
   makes a running Pod unreproducible.
3. Storage classes: the claims use the cluster default class. On a `local-path`
   single-node setup, storage is pinned to one node, so keep the Pod on that node
   with an overlay such as:

   ```yaml
   spec:
     template:
       spec:
         nodeSelector:
           kubernetes.io/hostname: node02-ip02
   ```

   Apply it with `kubectl kustomize <overlay>` or a small overlay directory; do
   not edit the base manifest for one machine.
4. A private registry needs an `imagePullSecrets` entry on the Pod template and
   a CA bundle for the node runtime. Both are environment-specific and are not
   committed here.

## Apply and verify

```bash
kubectl apply -k docker/k8s
kubectl -n evalscope rollout status deployment/evalscope-redis
kubectl -n evalscope rollout status deployment/evalscope
kubectl -n evalscope get pods
kubectl -n evalscope logs deployment/evalscope -c web
kubectl -n evalscope logs deployment/evalscope -c worker
kubectl -n evalscope port-forward svc/evalscope 9000:9000
```

Checks that matter:

- `kubectl -n evalscope get endpoints evalscope` has one address only once
  `/ready` passes; a Pod that is running but not ready has no endpoint.
- `kubectl -n evalscope exec deploy/evalscope -c worker -- python -c "import os, redis; print(redis.from_url(os.environ['EVALSCOPE_REDIS_URL']).keys('rq:worker:*'))"`
  returns at least one key.
- `kubectl -n evalscope exec deploy/evalscope -c web -- curl -fsS localhost:9000/ready`
  succeeds.
- A submitted task appears in the queue and is consumed by the `worker`
  container, not by the web process.

## Restart and interruption semantics

`strategy: Recreate` and one replica keep a single writer on the outputs claim.
Restarting the Pod interrupts any running task: the worker stops its AIPerf
subprocess, imports the partial tail when the artifacts allow it, and the run is
reported as stopped or partial rather than as a completed measurement.
`terminationGracePeriodSeconds: 60` leaves room for that cleanup; do not shorten
it below the AIPerf shutdown budget.

Redis keeps the queue across web restarts through AOF. Losing the Redis claim
loses queued and finished job records, not finished artifacts on the outputs
claim.

## Backup and rollback

- Outputs: snapshot or copy the `evalscope-data` claim; it holds task archives,
  plan manifests and reports.
- Queue: `kubectl -n evalscope exec deploy/evalscope-redis -- redis-cli BGSAVE`
  before a planned migration, then copy the Redis claim.
- Rollback: `kubectl -n evalscope rollout undo deployment/evalscope` (the image
  tag is immutable, so the previous ReplicaSet is reproducible).

## Known limits

- No high availability: one web writer, one worker, one Redis.
- No horizontal worker scaling: the outputs claim is `ReadWriteOnce` and plan
  runs own their task directory exclusively.
- No GPU scheduling stanza: add `resources.limits.nvidia.com/gpu` in an overlay
  when the target cluster exposes the device plugin.
