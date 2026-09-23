"""Worker environments and the ``cloud_hosted`` deploy bundle (ARCHITECTURE-V2 D-V2-3).

Three views of the same environment:

* :func:`worker_env` — the **decrypted** child environment for the supervisor
  (``GET /internal/v1/connections/{id}/worker-env``, service token only).
* :func:`worker_env_template` — a **redacted** template for an operator running
  an ``external`` pool (``GET /v1/connections/{id}/worker-env?format=env|compose|lk``).
* :func:`deploy_bundle` — a zip with ``livekit.toml``, ``secrets.env`` and a
  README for ``lk agent create/deploy`` (``cloud_hosted``; Phase 1 is bundle +
  instructions only). Secrets in it are placeholders: the platform never hands
  a decrypted secret to an admin or browser caller.

The worker reads ``LIVEKIT_AGENT_NAME`` today and ``LKAP_AGENT_NAME`` once
V2-07 lands, so both are always emitted with the connection's agent name.
"""

from __future__ import annotations

import io
import os
import shlex
import zipfile
from urllib.parse import urlparse

from lkap_contracts.fleet import WorkerEnv

from lkap_api.connections.clients import ConnectionCredentials
from lkap_api.db.models import LiveKitConnection
from lkap_api.settings import Settings

#: Formats accepted by the admin ``worker-env`` endpoint.
TEMPLATE_FORMATS: tuple[str, ...] = ("env", "compose", "lk")

#: Observability variables copied from the api's own environment when set.
OTEL_PASSTHROUGH: tuple[str, ...] = ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_HEADERS")

_SECRET_PLACEHOLDER = "<{name}>"


def api_base_url(settings: Settings) -> str:
    """The api url a worker should call back.

    ``LKAP_PUBLIC_BASE_URL`` when set, else the api on this host (the
    subprocess backend's case). Docker/cloud workers need the public url.
    """
    if settings.public_base_url:
        return settings.public_base_url.rstrip("/")
    return f"http://127.0.0.1:{settings.port}"


def _shared_env(row: LiveKitConnection, settings: Settings) -> dict[str, str]:
    env = {
        "LKAP_AGENT_NAME": row.agent_name,
        "LIVEKIT_AGENT_NAME": row.agent_name,
        "LKAP_CONNECTION_ID": row.id,
        "LKAP_API_BASE_URL": api_base_url(settings),
        "LKAP_PACKS": ",".join(settings.packs_list),
        "OTEL_SERVICE_NAME": "lkap-agent",
    }
    for name in OTEL_PASSTHROUGH:
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


def worker_env(row: LiveKitConnection, creds: ConnectionCredentials, settings: Settings) -> WorkerEnv:
    """The complete, decrypted environment of one pool replica. **Never log it.**

    Args:
        row: The connection.
        creds: Its decrypted credentials (from the client factory).
        settings: Api settings supplying the service token, packs and base url.

    Returns:
        ``WorkerEnv`` with ``LIVEKIT_URL/API_KEY/API_SECRET``, ``LKAP_AGENT_NAME``,
        ``LIVEKIT_AGENT_NAME``, ``LKAP_CONNECTION_ID``, ``LKAP_API_BASE_URL``,
        ``LKAP_SERVICE_TOKEN``, ``LKAP_PACKS`` and ``OTEL_*``.
    """
    env = {
        "LIVEKIT_URL": creds.url,
        "LIVEKIT_API_KEY": creds.api_key,
        "LIVEKIT_API_SECRET": creds.api_secret,
        "LKAP_SERVICE_TOKEN": settings.service_token,
        **_shared_env(row, settings),
    }
    return WorkerEnv(env=env, image=row.worker_image, agent_name=row.agent_name)


def redacted_env(row: LiveKitConnection, settings: Settings, fingerprint: str) -> dict[str, str]:
    """The worker environment with every secret replaced by a placeholder.

    Args:
        row: The connection.
        settings: Api settings.
        fingerprint: The key fingerprint (``…abcd``) shown next to the key placeholder.

    Returns:
        An ordered mapping safe to show to an admin.
    """
    return {
        "LIVEKIT_URL": row.url,
        "LIVEKIT_API_KEY": f"<LIVEKIT_API_KEY {fingerprint}>",
        "LIVEKIT_API_SECRET": _SECRET_PLACEHOLDER.format(name="LIVEKIT_API_SECRET"),
        "LKAP_SERVICE_TOKEN": _SECRET_PLACEHOLDER.format(name="LKAP_SERVICE_TOKEN"),
        **_shared_env(row, settings),
    }


def worker_env_template(row: LiveKitConnection, settings: Settings, fingerprint: str, fmt: str) -> str:
    """Render the redacted worker environment for an ``external`` pool.

    Args:
        row: The connection.
        settings: Api settings.
        fingerprint: The key fingerprint.
        fmt: ``env`` (dotenv lines), ``compose`` (a docker compose service) or
            ``lk`` (LiveKit CLI + ``uv run`` shell commands).

    Returns:
        Plain text; every secret is a ``<NAME>`` placeholder.

    Raises:
        ValueError: On an unknown format.
    """
    env = redacted_env(row, settings, fingerprint)
    header = (
        f"LKAP worker for connection {row.name!r} ({row.slug}); agent name {row.agent_name!r}.\n"
        "Replace every <...> placeholder; the api never shows secrets."
    )
    match fmt:
        case "env":
            lines = [f"# {line}" for line in header.splitlines()]
            lines += [f"{name}={value}" for name, value in env.items()]
            return "\n".join(lines) + "\n"
        case "compose":
            lines = [f"# {line}" for line in header.splitlines()]
            lines += [
                "services:",
                f"  lkap-agent-{row.slug}:",
                f"    image: lkap-agent:{row.worker_image}",
                '    command: ["python", "-m", "lkap_agent.main", "start"]',
                "    restart: unless-stopped",
                "    stop_grace_period: 60m",
                "    environment:",
            ]
            lines += [f'      {name}: "{value}"' for name, value in env.items()]
            return "\n".join(lines) + "\n"
        case "lk":
            exports = " \\\n  ".join(f"{name}={shlex.quote(value)}" for name, value in env.items())
            return (
                "\n".join(f"# {line}" for line in header.splitlines())
                + "\n\n# 1. Register the project with the LiveKit CLI (once).\n"
                + f"lk project add {shlex.quote('lkap-' + row.slug)} \\\n"
                + f"  --url {shlex.quote(row.url)} \\\n"
                + "  --api-key '<LIVEKIT_API_KEY>' --api-secret '<LIVEKIT_API_SECRET>'\n\n"
                + "# 2. Start one worker for this connection from the repository's agent/ directory.\n"
                + "cd agent && \\\n  "
                + exports
                + " \\\n  uv run python -m lkap_agent.main start\n"
            )
        case _:
            raise ValueError(f"unknown worker-env format {fmt!r}; use one of {', '.join(TEMPLATE_FORMATS)}")


def cloud_subdomain(url: str) -> str | None:
    """The LiveKit Cloud project subdomain of a ``*.livekit.cloud`` url, else ``None``."""
    host = (urlparse(url).hostname or "").lower()
    if not host.endswith(".livekit.cloud"):
        return None
    return host.split(".", 1)[0] or None


def deploy_bundle(row: LiveKitConnection, settings: Settings, fingerprint: str) -> bytes:
    """Build the ``cloud_hosted`` deploy bundle for one connection.

    The bundle is a per-connection directory (``lk`` cross-checks the project
    subdomain in ``livekit.toml``, so connections never share one) holding:

    * ``livekit.toml`` — the project subdomain; ``lk agent create`` adds the agent id.
    * ``secrets.env`` — the ``LKAP_*`` worker environment with the service token
      as a placeholder. LiveKit Cloud supplies ``LIVEKIT_URL/API_KEY/API_SECRET``
      to the agents it hosts.
    * ``README.md`` — the commands to run.

    Args:
        row: A ``cloud`` connection.
        settings: Api settings; ``public_base_url`` must be the api's public HTTPS url.
        fingerprint: The key fingerprint for the README.

    Returns:
        The zip archive bytes.
    """
    subdomain = cloud_subdomain(row.url) or "<project-subdomain>"
    folder = f"lkap-{row.slug}"
    project = f"lkap-{row.slug}"
    env = {
        name: value
        for name, value in redacted_env(row, settings, fingerprint).items()
        if not name.startswith("LIVEKIT_") or name == "LIVEKIT_AGENT_NAME"
    }
    toml = f'[project]\n  subdomain = "{subdomain}"\n'
    secrets = (
        "# LKAP worker secrets for `lk agent create --secrets-file secrets.env`.\n"
        "# Replace <LKAP_SERVICE_TOKEN> before uploading.\n"
        + "".join(f"{name}={value}\n" for name, value in env.items())
    )
    readme = f"""# Deploy the LKAP worker to LiveKit Cloud: {row.name}

Connection `{row.slug}` · project `{subdomain}` · agent name `{row.agent_name}` · image `{row.worker_image}` ·
key `{fingerprint}`.

This directory belongs to this connection only. It deploys exactly one agent, named
`{row.agent_name}`; it never touches other agents in the same LiveKit project.

1. Copy `livekit.toml` and `secrets.env` into a copy of the repository's `agent/` directory.
2. Replace `<LKAP_SERVICE_TOKEN>` in `secrets.env` with the api's service token.
3. Register the project with the LiveKit CLI (once):

   ```sh
   lk project add {project} --url {row.url} --api-key <LIVEKIT_API_KEY> --api-secret <LIVEKIT_API_SECRET>
   ```

4. Create the agent, then deploy updates:

   ```sh
   lk agent create --project {project} --secrets-file secrets.env
   lk agent deploy --project {project}
   ```

The hosted worker calls the api at `{api_base_url(settings)}`, which must be reachable over public HTTPS.
Check `lk agent create --help` for the flags of your CLI version.
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{folder}/livekit.toml", toml)
        archive.writestr(f"{folder}/secrets.env", secrets)
        archive.writestr(f"{folder}/README.md", readme)
    return buffer.getvalue()
