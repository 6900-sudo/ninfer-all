# NInfer cloud engine for OSINT Deep Search

This directory defines the first cloud-service layer around NInfer. NInfer remains the GPU inference engine; the gateway is the controlled API boundary used by OSINT Deep Search.

## Request path

Phone / OSINT Deep Search → HTTPS (Caddy) → authenticated gateway → private Docker network → NInfer GPU server.

Only Caddy publishes internet-facing ports: TCP 443 for HTTPS and UDP 443 for optional HTTP/3. No HTTP port is published. Caddy can obtain a certificate through ACME TLS-ALPN-01 on TCP 443 when the domain's DNS points to the host and that port is reachable. The gateway and NInfer have no public host ports. NInfer sits on an isolated internal network; a separate one-shot downloader has outbound access, downloads the pinned model artifact and verifies its SHA-256 before NInfer is allowed to start.

The gateway accepts a separate client bearer key, then uses the private NInfer key upstream. The NInfer launcher passes that engine key to the server's own `--api-key` option. The engine's request handler checks the bearer key or `x-api-key` header on API routes; `/health` is intentionally exempt for container health checks. The gateway exposes only model listing and chat completions, caps request size and output tokens, applies a single-process request rate limit (30 requests/minute by default), supports streamed responses, and does not log prompts or completions. CORS and interactive API documentation are disabled.

## Current status

- The cloud gateway tests, shell syntax checks, Compose validation and gateway container build passed in [gateway CI run 42360678666](https://github.com/6900-sudo/ninfer-all/actions/runs/42360678666).
- The fork's CUDA 13.4 / sm86 NInfer image build and GHCR publish step completed successfully in [inference image run 42360545186](https://github.com/6900-sudo/ninfer-all/actions/runs/42360545186).
- The workflow published tag `ghcr.io/6900-sudo/ninfer-all:cloud-cuda13.4-sm86-2f15976d6f1afb2b400d5f9105f8a018417ac253` with registry digest `sha256:0dfdde6f22f4f85da1374ac5994265d986d6adab42431605aede48cce40e81aa`. It was built from commit `2f15976d6f1afb2b400d5f9105f8a018417ac253`; the current branch head is later documentation-only commit `5b34198c58f1508559901fe4b297c0e1a68b8a39`.
- The GHCR tag was pushed by Actions, but anonymous pull access and package visibility could not be verified through the available GitHub connection. If the package is private, a user-controlled read credential or an explicit package-visibility decision will be needed. This project will not change package visibility or credentials.
- The image has not been smoke-tested on a real GPU. A successful container build does not prove runtime compatibility, model loading, throughput or VRAM fit.
- NInfer needs a supported NVIDIA GPU and a compatible NVIDIA driver / Container Toolkit on the host. CPU-only hosting and Intel UHD are not supported.
- The initial model is Qwen3.8-27B. The downloader pins revision `1cbd84e7221e51186bd7f093a149912d2489625b`, checks the expected file size and SHA-256, and only then promotes the download to its final filename. The Compose service does not set the optional checksum-bypass variable.
- The gateway currently uses one shared client key and a single-process global rate limiter. Before multi-user or multi-replica access, add per-user identity, revocation, distributed quotas, durable audit metadata and tenant isolation.
- This stack does not yet provide OSINT job queues, evidence storage, scheduled monitoring, or the OSINT front end. Those belong in the OSINT application layer, not the inference engine.
- GPU hosting, storage, a domain and any long-running cloud resources are not provisioned. No cloud resources have been created by this repository work.

## Configuration (not yet authorised for deployment)

1. On the eventual GPU host, create a private `.env` from `.env.example`.
2. Set a real domain in `AI_DOMAIN` and point its DNS A/AAAA records to the host.
3. Generate two different strong random secrets for `GATEWAY_API_KEY` and `NINFER_API_KEY`. Keep the file private; Docker administrators can inspect container configuration, so restrict host access.
4. Confirm that the GHCR package can be pulled by the host. If it is private, arrange a narrowly scoped read credential yourself; do not put credentials in the repository.
5. Select a GPU supported by this NInfer build and confirm the exact model, licence, VRAM and context settings before provisioning.
6. Permit inbound TCP 443 only (plus UDP 443 if HTTP/3 is wanted). Do not publish ports 80, 8080 or 8000. Keep SSH restricted to the administrator's own access path.
7. Start with a low context and concurrency setting. Increase only after measuring peak VRAM and host RAM with the actual model.

The deployment procedure will be finalised after package access and a chosen GPU host have passed verification. Do not run a public deployment using the example secrets.

## API

Base URL: `https://AI_DOMAIN/v1`

- `GET /models`
- `POST /chat/completions`

Send `Authorization: Bearer GATEWAY_API_KEY`. The gateway replaces that credential with the private NInfer key before forwarding. The inference server's own `/health` route is not exposed publicly.

## Verification limits

CI verifies the gateway, tests, Compose syntax and the build/publish job. It does not test an actual GPU host or send a real request through a deployed Caddy → gateway → NInfer stack. A GPU-host smoke test with a licensed model artifact is still required.

## First GPU-host candidate (not yet provisioned)

For a first real-GPU smoke test, the leading candidate is a single **RunPod L40S (48 GB VRAM)**. NInfer's own GPU table lists the L40S with a built-in device profile and a measured Flash-Next run. The extra VRAM gives more room for model weights, KV cache and concurrent requests than a 24 GB card. This is a candidate, not a guarantee that every model recipe will fit: validate the exact model, context and concurrency on the instance.

RunPod's published Pods price page displayed **US$1.09 per hour** for an L40S when checked on 7 November 2026. At that rate, 10 test hours are about **US$10.90**; 40 hours are **US$43.60**; continuous 730-hour operation is about **US$795.70**, before storage, bandwidth, taxes or other charges. RunPod lists volume storage separately (running and idle rates differ). Confirm the actual region, instance class and checkout price before launching.

- NInfer supported-card evidence: [docs/gpus.md](../docs/gpus.md)
- RunPod's live pricing: https://www.runpod.io/pricing?sub1=model-compare

The first deployment should be a short, manually stopped test window, not an always-on server. Before any real investigation data is sent, check the provider's region, data-processing terms, retention/deletion arrangements and applicable UK data-protection obligations. A provider's “secure” tier is not, by itself, proof of legal compliance.
