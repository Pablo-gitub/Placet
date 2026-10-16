import { vi } from "vitest";

/** EventSource stand-in: opens at once; tests can emit named events. */
export class MockEventSource {
  static last = null;

  constructor(url) {
    this.url = url;
    this.listeners = {};
    MockEventSource.last = this;
    setTimeout(() => this.onopen?.(), 0);
  }

  addEventListener(event, callback) {
    this.listeners[event] = callback;
  }

  emit(event, data) {
    this.listeners[event]?.({ data: JSON.stringify(data) });
  }

  close() {}
}

export const json = (body, status = 200) =>
  Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) });

/**
 * fetch mock: `routes` maps a matcher (exact path, or a function of url and
 * options) to a response (value or function returning a value/promise).
 */
export function mockFetch(routes) {
  const fn = vi.fn((url, options = {}) => {
    for (const [match, respond] of routes) {
      const hit = typeof match === "function" ? match(url, options) : url === match;
      if (hit) {
        const result = typeof respond === "function" ? respond(url, options) : respond;
        return result && typeof result.then === "function" ? result : json(result);
      }
    }
    return Promise.reject(new Error(`Unexpected fetch ${options.method || "GET"} ${url}`));
  });
  global.fetch = fn;
  return fn;
}

export const transcriptOf = (sessionId) => (url) =>
  url.startsWith(`/api/sessions/${sessionId}/transcript`);

export const paramsOf = (url) => new URL(url, "http://x").searchParams;
