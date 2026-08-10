(function () {
  const token = window.NEXUS_ADAPTER_TOKEN;

  async function call(path, { method = "GET", body } = {}) {
    const options = {
      method,
      headers: { "X-Nexus-Token": token },
      credentials: "same-origin",
    };
    if (body !== undefined) {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }

    const response = await fetch(path, options);
    let payload = null;
    try { payload = await response.json(); } catch (_) {}

    if (response.status === 409) {
      const error = new Error("effect_busy");
      error.code = "EFFECT_BUSY";
      error.payload = payload;
      throw error;
    }
    if (response.status === 504) {
      const error = new Error("runner_timeout_ambiguous");
      error.code = "AMBIGUOUS_TIMEOUT";
      error.payload = payload;
      throw error;
    }
    if (!response.ok) {
      const error = new Error(`adapter_error_${response.status}`);
      error.code = "ADAPTER_ERROR";
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  window.NexusAdapter = Object.freeze({
    getStatus: () => call("/api/mission/status"),
    getLog: (runRef) => call(`/api/mission/log?run_ref=${encodeURIComponent(runRef)}`),
    prepare: () => call("/api/mission/prepare", { method: "POST" }),
    simulate: (effect, approval) => call(`/api/mission/${effect}/simulate`, {
      method: "POST", body: { approval }
    }),
    broadcast: (effect, approval) => call(`/api/mission/${effect}/broadcast`, {
      method: "POST", body: { approval }
    }),
    bind: (effect) => call(`/api/mission/${effect}/bind`, { method: "POST" }),
    verify: (effect) => call(`/api/mission/${effect}/verify`, { method: "POST" }),
  });
})();
