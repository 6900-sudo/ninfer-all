# NInfer cloud engine for OSINT Deep Search

This directory defines the first cloud-service layer around NInfer. NInfer remains the GPU inference engine; the gateway is the controlled API boundary used by OSINT Deep Search.

## Request path

Phone / OSINT Deep Search → HTTPS (Caddy) → authenticated gateway → private Docker network → NInfer GPU server.

Only Caddy publishes internet-facing ports (80/443). The gateway and NInfer have no public host ports. The gateway accepts a separate client bearer key, then uses the private NInfer key upstream. It exposes only model listing and chat completions, caps request size and output tokens, supports streamed responses, and does not log prompts or completions. CORS and interactive API documentation are disabled.

## Current status and limitations

- The gateway and Compose stack are an initial foundation, not a deployed service.
- This fork currently has no Actions run history and no published release. Its own GHCR image has not yet been built and verified.
- The sample image in `.env.example` is the upstream image as a temporary bootstrap reference. Do not treat it as a fork-built release. Replace it with a tested `ghcr.io/6900-sudo/ninfer-all` tag once the fork's image pipeline has succeeded.
- NInfer needs a supported NVIDIA GPU and a compatible NVIDIA driver / Container Toolkit on the host. CPU-only hosting and Intel UHD are not supported.
- The example model ID is a starting default, not a final model selection. Confirm artifact availability, model licence, GPU VRAM and context settings before provisioning.
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

The deployment command and model-download procedure will be added after a fork-built image and a chosen GPU host have passed verification. Do not run a public deployment using the example secrets.

## API

Base URL: `https://AI_DOMAIN/v1`

- `GET /models`
- `POST /chat/completions`

Send `Authorization: Bearer GATEWAY_API_KEY`. The gateway replaces that credential with the private NInfer key before forwarding. The inference server's own `/health` route is not exposed publicly.

## Verification

The gateway tests cover authentication, request limits, upstream routing and readiness. CI builds the gateway container and runs the tests. A successful gateway CI run does not prove the CUDA engine works on a real GPU; that requires a GPU-host smoke test with a licensed model artifact.
