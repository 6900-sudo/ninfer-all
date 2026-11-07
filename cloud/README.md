# NInfer cloud engine for OSINT Deep Search

This directory defines the first cloud-service layer around NInfer. NInfer remains the GPU inference engine; the gateway is the controlled API boundary used by OSINT Deep Search.

## Request path

Phone / OSINT Deep Search → HTTPS (Caddy) → authenticated gateway → private Docker network → NInfer GPU server.

Only Caddy publishes internet-facing ports (80/443). The gateway and NInfer have no public host ports. NInfer sits on an isolated internal network; a separate one-shot downloader has outbound access, downloads the pinned model artifact and verifies its SHA-256 before NInfer is allowed to start. The gateway accepts a separate client bearer key, then uses the private NInfer key upstream. It exposes only model listing and chat completions, caps request size and output tokens, supports streamed responses, and does not log prompts or completions. CORS and interactive API documentation are disabled.

## Current status and limitations

- The gateway and Compose stack are an initial foundation, not a deployed service.
- This fork currently has no Actions run history and no published release. Its own GHCR image has not yet been built and verified.
- The sample image in `.env.example` is the upstream image as a temporary bootstrap reference. Do not treat it as a fork-built release. Replace it with a tested `ghcr.io/6900-sudo/ninfer-all` tag once the fork's image pipeline has succeeded.
- NInfer needs a supported NVIDIA GPU and a compatible NVIDIA driver / Container Toolkit on the host. CPU-only hosting and Intel UHD are not supported.
- The initial model is Qwen3.8-27B: its published NInfer artifact is about 19 GiB and declares Apache-2.0. The downloader pins a specific revision and checks its SHA-256 before use. Confirm model suitability, GPU VRAM and context settings before provisioning.
- The gateway currently uses one shared client key. Before multi-user access, add per-user identity, revocation, quotas, durable audit metadata and tenant isolation.
- This stack does not yet provide OSINT job queues, evidence storage, scheduled monitoring, or the OSINT front end. Those belong in the OSINT application layer, not the inference engine.
- GPU hosting is paid. No provider, instance, domain or billing account is provisioned by this repository.

## Configuration

1. On the eventual GPU host, create a private `.env` from `.env.example`.
2. Set a real domain in `AI_DOMAIN` and point its DNS A/AAAA records to the host.
3. Generate two different strong random secrets for `GATEWAY_API_KEY` and `NINFER_API_KEY`. Keep the file private.
4. Select a GPU supported by the chosen NInfer build and set the image tag and model ID only after checking compatibility and licence.
5. Permit inbound TCP 80/443 only. Do not publish ports 8080 or 8000. Keep SSH restricted to the administrator's own access path.
6. Start with a low context and concurrency setting. Increase only after measuring peak VRAM and host RAM with the actual model.

The deployment procedure will be finalised after the fork-built image and a chosen GPU host have passed verification. Do not run a public deployment using the example secrets.

## API

Base URL: `https://AI_DOMAIN/v1`

- `GET /models`
- `POST /chat/completions`

Send `Authorization: Bearer GATEWAY_API_KEY`. The gateway replaces that credential with the private NInfer key before forwarding. The inference server's own `/health` route is not exposed publicly.

## Verification

The gateway tests cover authentication, request limits, upstream routing and readiness. CI builds the gateway container and runs the tests. A successful gateway CI run does not prove the CUDA engine works on a real GPU; that requires a GPU-host smoke test with a licensed model artifact.

## First GPU-host candidate (not yet provisioned)

For a first real-GPU smoke test, the leading candidate is a single **RunPod L40S (48 GB VRAM)**. NInfer's own GPU table lists the L40S with a built-in device profile and a measured Flash-Next run. The extra VRAM gives more room for model weights, KV cache and concurrent requests than a 24 GB card. This is a candidate, not a guarantee that every model recipe will fit: validate the exact model, context and concurrency on the instance.

RunPod's published Pods price page displayed **US$1.09 per hour** for an L40S when checked on 7 November 2026. At that rate, 10 test hours are about **US$10.90**; 40 hours are **US$43.60**; continuous 730-hour operation is about **US$795.70**, before storage, bandwidth, taxes or other charges. RunPod lists volume storage separately (running and idle rates differ). Confirm the actual region, instance class and checkout price before launching.

- NInfer supported-card evidence: [docs/gpus.md](../docs/gpus.md)
- RunPod's live pricing: https://www.runpod.io/pricing?sub1=model-compare

The first deployment should be a short, manually stopped test window, not an always-on server. Before any real investigation data is sent, check the provider's region, data-processing terms, retention/deletion arrangements and applicable UK data-protection obligations. A provider's “secure” tier is not, by itself, proof of legal compliance.
