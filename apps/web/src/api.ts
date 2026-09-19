export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(`${message} (${code})`);
  }
}
export async function api<T>(
  path: string,
  body?: unknown,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    signal: AbortSignal.timeout(12_000),
    ...options,
    method: options.method ?? (body === undefined ? "GET" : "POST"),
    headers: {
      ...(body === undefined ? {} : { "content-type": "application/json" }),
      ...options.headers,
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (response.status === 204) return undefined as T;
  const result = await response.json().catch(() => {
    throw new ApiError(
      response.status,
      "INVALID_RESPONSE",
      "The API is unavailable or returned an incomplete response",
    );
  });
  if (!response.ok)
    throw new ApiError(
      response.status,
      result.error?.code ?? "REQUEST_FAILED",
      result.error?.message ?? "Request failed",
    );
  return result as T;
}
export const message = (error: unknown) =>
  error instanceof Error ? error.message : "An unexpected error occurred";

export const isSessionEnded = (error: unknown) =>
  error instanceof ApiError &&
  error.status === 410 &&
  error.code === "SESSION_ENDED";

export async function endSession(sessionId: string): Promise<void> {
  await api(`/api/demo-sessions/${sessionId}/end`, {});
}

/** Best effort only: page dismissal cannot wait for a response. */
export function beaconEndSession(sessionId: string): void {
  const path = `/api/demo-sessions/${sessionId}/end`;
  if (navigator.sendBeacon(path)) return;
  void fetch(path, {
    method: "POST",
    credentials: "same-origin",
    keepalive: true,
  });
}
