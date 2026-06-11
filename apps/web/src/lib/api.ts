import type {
  MediaConfigResponse,
  MediaLinkingRequest,
  MediaLinkingResponse,
  MediaObjectsResponse,
  RenderJobCreate,
  RenderJobStatus,
  RenderOutputUrlResponse,
  TranscodeJobCreate,
  TranscodeResponse,
} from "@/lib/types";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export function apiUrl(path: string): string {
  const normalizedBaseUrl = API_BASE_URL.endsWith("/")
    ? API_BASE_URL.slice(0, -1)
    : API_BASE_URL;
  return `${normalizedBaseUrl}${path.startsWith("/") ? path : `/${path}`}`;
}

export async function getMediaConfig(): Promise<MediaConfigResponse> {
  return fetchJson<MediaConfigResponse>("/media/config");
}

export async function listMediaObjects(params: {
  prefix?: string;
  continuationToken?: string | null;
  maxKeys?: number | null;
} = {}): Promise<MediaObjectsResponse> {
  const searchParams = new URLSearchParams();

  if (params.prefix) {
    searchParams.set("prefix", params.prefix);
  }
  if (params.continuationToken) {
    searchParams.set("continuation_token", params.continuationToken);
  }
  if (params.maxKeys) {
    searchParams.set("max_keys", String(params.maxKeys));
  }

  const query = searchParams.toString();
  return fetchJson<MediaObjectsResponse>(`/media/objects${query ? `?${query}` : ""}`);
}

export async function createMediaLinking(
  request: MediaLinkingRequest,
): Promise<MediaLinkingResponse> {
  return fetchJson<MediaLinkingResponse>("/media/linking", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

export async function createRenderJob(
  job: RenderJobCreate,
): Promise<RenderJobStatus> {
  return fetchJson<RenderJobStatus>("/jobs", {
    method: "POST",
    body: JSON.stringify(job),
  });
}

export async function createTranscodeJob(
  job: TranscodeJobCreate,
): Promise<TranscodeResponse> {
  return fetchJson<TranscodeResponse>("/transcode", {
    method: "POST",
    body: JSON.stringify(job),
  });
}

export async function getCurrentRenderJob(): Promise<RenderJobStatus | null> {
  const response = await fetch(apiUrl("/jobs/current"), {
    credentials: "include",
    headers: {
      Accept: "application/json",
    },
  });

  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with ${response.status}`);
  }

  return response.json() as Promise<RenderJobStatus>;
}

export async function getRenderJob(jobId: string): Promise<RenderJobStatus> {
  return fetchJson<RenderJobStatus>(`/jobs/${jobId}`);
}

export async function cancelCurrentRenderJob(): Promise<RenderJobStatus> {
  return fetchJson<RenderJobStatus>("/jobs/current", {
    method: "DELETE",
  });
}

export async function getRenderOutputUrl(
  jobId: string,
): Promise<RenderOutputUrlResponse> {
  return fetchJson<RenderOutputUrlResponse>(`/jobs/${jobId}/output/url`);
}

async function fetchJson<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch(apiUrl(path), {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...init.headers,
    },
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(parseApiError(body, response.status));
  }

  return response.json() as Promise<T>;
}

export function parseApiError(body: string, status: number): string {
  if (!body) {
    return `Request failed with ${status}`;
  }

  try {
    const parsed = JSON.parse(body) as {
      detail?: string | Array<{ msg?: string }>;
    };

    if (typeof parsed.detail === "string" && parsed.detail.trim()) {
      return parsed.detail;
    }

    if (Array.isArray(parsed.detail) && parsed.detail.length > 0) {
      return parsed.detail
        .map((item) => item.msg ?? JSON.stringify(item))
        .join("; ");
    }
  } catch {
    // Fall back to the raw response body.
  }

  return body;
}
