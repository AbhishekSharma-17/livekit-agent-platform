# Brief: run the LKAP agent worker (with its turn-detection models) on the DGX

For a Claude Code session running **on the DGX** (the machine that hosts the self-hosted LiveKit server). Written 2026-09-30.

## Goal

Run one LKAP agent worker on the DGX, next to the LiveKit server, for the LKAP connection **"DGX-LivekitServer"** (agent name `lkap-dgx`). The worker must carry the end-of-turn models so that self-hosted agents whose speech-to-text has no end-of-turn detection of its own can still use the LiveKit turn detector, locally on the DGX.

Today that worker runs on the developer's Mac and reaches the DGX over a relayed Tailscale path, which adds about 1.2 s per reply. Moving it onto the DGX removes that path. Nothing about the LiveKit server itself changes.

## Why a worker, not a separate "turn-detection server"

- The LiveKit turn detector (open weights, ONNX) and the Silero voice-activity model are **in-process libraries**. The worker loads them and runs them on CPU in a few tens of milliseconds per turn. LiveKit ships no network server for them; the hosted version exists only inside LiveKit Cloud's Inference gateway.
- So "deploy the turn-detection model on the DGX" means: **run the worker on the DGX with the model files baked in**. The agent Dockerfile already runs `livekit.agents download-files` at build, which bakes the turn detector and Silero weights into the image. No GPU is needed.
- LKAP picks the detector itself: on the DGX connection (no hosted Inference) it uses the **local** turn detector. When an agent's speech-to-text decides turns itself (Deepgram Flux, `capabilities.end_of_turn`), the worker uses that and skips the model. You don't configure this per agent.
- A standalone detection service would add a network hop per turn and need a custom LKAP plugin. **Do not build one** unless the coordinator asks.

## What you need from the user (never in the repo, never in logs)

1. **The worker settings.** In the LKAP console: **Connections → DGX-LivekitServer → Copy worker settings**. This copies an env file whose secrets are `<…>` placeholders: `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LKAP_SERVICE_TOKEN`, plus `LKAP_AGENT_NAME=lkap-dgx`, `LKAP_CONNECTION_ID`, `LKAP_API_BASE_URL`, `LKAP_PACKS`. The user fills in the real values on the DGX. Store the file as `~/.config/lkap/worker-dgx.env`, mode `0600`, outside any git checkout.
2. **The api address reachable from the DGX.** The LKAP api runs on the developer's Mac. The coordinator session on the Mac will expose it on the Mac's Tailscale address (for example `http://<mac-tailscale-ip>:8080`). Put that in `LKAP_API_BASE_URL`, not `127.0.0.1`.
3. **`LIVEKIT_URL`.** Because the worker now sits on the same machine as the server, prefer the local address (for example `ws://127.0.0.1:7880`, or whatever the server listens on) over the Tailscale hostname. Callers keep using the public/Tailscale URL, which is stored on the connection in LKAP and is unaffected.
4. `LKAP_PACKS=packs.insurance_claim,packs.generic`. It must match the api's value.

## Steps

1. **Code.** `git clone https://github.com/AbhishekSharma-17/livekit-agent-platform` (or pull), then check out `main`.
2. **Build the image** (DGX Spark is **arm64**; `python:3.12-slim-bookworm` is multi-arch, so build natively on the DGX):
   ```bash
   cd livekit-agent-platform
   scripts/vendor_agent_deps.sh
   docker build -f agent/Dockerfile --build-arg LKAP_IMAGE_FLAVOR=slim -t lkap-agent:dgx agent
   ```
   Confirm the build log shows `download-files` completing (turn detector plus Silero). If any provider plugin fails on arm64, report the package name. Don't patch the Dockerfile without asking the coordinator.
3. **Run it** with a restart policy, host networking (it talks to the local LiveKit server and to the api over Tailscale), and a long stop grace so live calls drain:
   ```bash
   docker run -d --name lkap-agent-dgx --restart unless-stopped \
     --network host --env-file ~/.config/lkap/worker-dgx.env \
     -e LKAP_WORKER_HTTP_PORT=0 --stop-signal SIGINT --stop-timeout 3600 \
     lkap-agent:dgx
   ```
   (Or the `agent:` service sketched in `deploy/docker-compose.yml` with the same env file.) Run **exactly one** `lkap-dgx` worker.
4. **Verify:**
   - `docker logs lkap-agent-dgx` shows `registered worker` with `"agent_name": "lkap-dgx"`, and no traceback.
   - The LKAP console's DGX-LivekitServer connection shows **1 worker ready**, reported from the DGX host.
   - On a call to a DGX agent whose speech-to-text is Deepgram Flux, the worker logs "the transcriber decides when the caller's turn ends". For an agent whose speech-to-text has no end-of-turn detection, the log shows the local turn detector in use and **no download at call time** (the files were baked in).
5. **Tell the coordinator** it's up (container id, image digest, first `registered worker` line with secrets removed). The coordinator then stops the temporary `lkap-dgx` worker on the Mac. Two workers under the same name would split calls, so don't leave both running longer than needed.

## Network note (the other half of the latency)

`tailscale ping` from the Mac to the DGX currently goes **via DERP (relay)**, not direct. Please check on the DGX: `tailscale status` and `tailscale netcheck`. If UDP 41641 is blocked by the DGX's firewall or network, allow it so the Mac and the DGX connect directly. With the worker on the DGX, this matters mainly for callers on the Mac and for the worker's calls to the api.

## Rules

- Never commit, print or paste real keys, secrets or the service token. Keep them only in the `0600` env file on the DGX.
- Don't change the LiveKit server's config or version (it runs 1.13.7, the latest release).
- Don't start a second LiveKit server, and don't expose new public ports.
- Report anything unexpected to the coordinator instead of working around it.
