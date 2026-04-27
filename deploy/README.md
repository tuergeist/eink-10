# Deployment

The server runs as a single-replica Deployment fronted by an Ingress that
terminates TLS. Image bytes live on a 1 Gi `ReadWriteOnce` PVC so pod
restarts don't lose state.

## Bootstrap

```bash
# 1. Build & push the image (adjust the registry to yours)
docker build -t ghcr.io/tuergeist/eink-10:latest server/
docker push ghcr.io/tuergeist/eink-10:latest

# 2. Create tokens
PUSH_TOKEN=$(openssl rand -hex 32)
READ_TOKEN=$(openssl rand -hex 32)

# 3. Adjust deploy/k8s/secret.example.yaml → secret.yaml
#    Adjust deploy/k8s/deployment.yaml: image, EINK_PUBLIC_BASE_URL
#    Adjust deploy/k8s/ingress.yaml: host, TLS, ingressClassName

# 4. Apply
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/secret.yaml         # not in git
kubectl apply -k deploy/k8s/                    # the rest via kustomize
```

## Verifying

```bash
# Health (no auth)
curl https://eink.example.com/healthz

# Push from anywhere (CI, cron, your renderer)
curl -X POST https://eink.example.com/image?dither=floyd-steinberg \
  -H "Authorization: Bearer $PUSH_TOKEN" \
  -H "Content-Type: image/png" \
  --data-binary @dashboard.png

# Read (this is what the Inkplate does, with READ_TOKEN baked into firmware)
curl -H "Authorization: Bearer $READ_TOKEN" https://eink.example.com/config.json
```

## Why single-replica + Recreate

The PVC is `ReadWriteOnce`, and there's only ever one canonical image. Two
pods would race on the file. If you ever need HA, switch to
`ReadWriteMany` (NFS / object-storage-backed) or move state to S3.
