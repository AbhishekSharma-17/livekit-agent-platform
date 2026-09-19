import { NextRequest, NextResponse } from "next/server";

/**
 * Server-side proxy for the console: forwards `/api/console/*` to the
 * `lkap_api` admin API, attaching `X-Admin-Token` from the server-only env
 * var `LKAP_ADMIN_TOKEN` (never sent to the browser bundle). See
 * docs/CONTRACTS.md §3, §7.
 *
 * Always dynamic (no caching): every call carries admin auth and often
 * mutates server state.
 */
export const dynamic = "force-dynamic";

const HOP_BY_HOP_REQUEST_HEADERS = new Set([
  "host",
  "connection",
  "content-length",
]);

function apiBaseUrl(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!base) {
    throw new Error("NEXT_PUBLIC_API_BASE_URL is not set");
  }
  return base.replace(/\/+$/, "");
}

function adminToken(): string {
  const token = process.env.LKAP_ADMIN_TOKEN;
  if (!token) {
    throw new Error(
      "LKAP_ADMIN_TOKEN is not set (server-only; console proxy cannot authenticate)",
    );
  }
  return token;
}

async function proxy(
  request: NextRequest,
  path: string[],
): Promise<NextResponse> {
  const upstreamUrl = new URL(`${apiBaseUrl()}/v1/${path.join("/")}`);
  upstreamUrl.search = request.nextUrl.search;

  const headers = new Headers();
  request.headers.forEach((value, key) => {
    if (!HOP_BY_HOP_REQUEST_HEADERS.has(key.toLowerCase())) {
      headers.set(key, value);
    }
  });
  headers.set("X-Admin-Token", adminToken());

  const hasBody = !["GET", "HEAD"].includes(request.method);
  const upstreamResponse = await fetch(upstreamUrl, {
    method: request.method,
    headers,
    body: hasBody ? await request.arrayBuffer() : undefined,
    // @ts-expect-error - `duplex` is required by undici for streamed bodies but missing from the DOM lib types.
    duplex: hasBody ? "half" : undefined,
    cache: "no-store",
  });

  const responseHeaders = new Headers(upstreamResponse.headers);
  responseHeaders.delete("x-admin-token");
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("content-length");

  return new NextResponse(upstreamResponse.body, {
    status: upstreamResponse.status,
    headers: responseHeaders,
  });
}

type RouteParams = { params: Promise<{ path: string[] }> };

async function handle(
  request: NextRequest,
  { params }: RouteParams,
): Promise<NextResponse> {
  const { path } = await params;
  return proxy(request, path);
}

export const GET = handle;
export const POST = handle;
export const PUT = handle;
export const PATCH = handle;
export const DELETE = handle;
