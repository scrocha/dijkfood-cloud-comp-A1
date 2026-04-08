export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public body?: unknown
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  url: string,
  init: RequestInit = {}
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> ?? {}),
  };

  const res = await fetch(url, {
    ...init,
    headers,
  });

  if (!res.ok) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      body = await res.text();
    }
    throw new ApiError(
      `HTTP ${res.status}: ${res.statusText}`,
      res.status,
      body
    );
  }

  const text = await res.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

export async function getJson<T = unknown>(url: string): Promise<T> {
  return request<T>(url, { method: "GET" });
}

export async function postJson<T = unknown>(url: string, body: unknown): Promise<T> {
  return request<T>(url, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function putJson<T = unknown>(url: string, body: unknown): Promise<T> {
  return request<T>(url, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export async function patchJson<T = unknown>(url: string, body: unknown): Promise<T> {
  return request<T>(url, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}
