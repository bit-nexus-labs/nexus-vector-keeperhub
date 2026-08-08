"use strict";

const byId = (id) => document.getElementById(id);

function text(id, value) {
  const node = byId(id);
  if (node) node.textContent = String(value ?? "—");
}

function boolText(value) {
  if (value === true) return "true";
  if (value === false) return "false";
  return "—";
}

function renderRuntimeBadge(liveEvidence) {
  const badge = byId("runtime-badge");
  if (!badge) return;
  badge.replaceChildren();
  badge.className = liveEvidence ? "live-badge" : "quiet-badge";
  if (liveEvidence) {
    const dot = document.createElement("i");
    dot.setAttribute("aria-hidden", "true");
    badge.append(dot, document.createTextNode(" LIVE TESTNET CANARY EVIDENCE"));
    return;
  }
  badge.textContent = "LOCAL READ-ONLY CONSOLE";
}

function setStage(id, stateClass, statusId, statusText) {
  const stage = byId(id);
  if (stage) stage.className = `stage${stateClass ? ` ${stateClass}` : ""}`;
  text(statusId, statusText);
}

function effectClass(action, state) {
  if (state === "CHAIN_CONFIRMED" || action === "SKIP_VERIFIED") {
    return "is-verified";
  }
  if (action === "EXECUTE_MISSING") return "is-ready";
  if (
    action === "RECONCILE_REQUIRED" ||
    action === "MANUAL_REVIEW_REQUIRED"
  ) {
    return "is-blocked";
  }
  return "";
}

function renderEffects(effects) {
  const list = byId("effect-list");
  list.replaceChildren();
  for (const effect of effects) {
    const card = document.createElement("article");
    card.className = `effect-card ${effectClass(
      effect.continuation_action,
      effect.effect_state
    )}`.trim();

    const top = document.createElement("div");
    const ref = document.createElement("span");
    ref.textContent = String(effect.effect_ref || "effect").toUpperCase();
    const amount = document.createElement("strong");
    amount.textContent = `${effect.amount || "—"} ${effect.asset || "USDC"}`;
    top.append(ref, amount);

    const state = document.createElement("p");
    state.textContent = `${effect.effect_state || "UNKNOWN"} · ${
      effect.reason || "NO_REASON"
    }`;

    const action = document.createElement("span");
    action.className = "effect-action";
    action.textContent = `NEXT ACTION · ${
      effect.continuation_action || "NO_ACTION"
    }`;

    card.append(top, state, action);
    list.append(card);
  }
}

function renderMissionPlaceholder(stopped) {
  const list = byId("effect-list");
  list.replaceChildren();
  const card = document.createElement("article");
  card.className = stopped ? "effect-card is-blocked" : "effect-card";
  const top = document.createElement("div");
  const label = document.createElement("span");
  label.textContent = stopped ? "STOP" : "PLAN";
  const amount = document.createElement("strong");
  amount.textContent = "—";
  top.append(label, amount);
  const copy = document.createElement("p");
  copy.textContent = stopped
    ? "Sanitized Mission plan was rejected."
    : "Sanitized Mission plan not loaded.";
  card.append(top, copy);
  list.append(card);
}

function renderCanary(canary) {
  const loaded = canary.loaded === true;
  const passed = loaded && canary.status === "PASS";
  const stopped = canary.status === "STOP";

  renderRuntimeBadge(passed);
  text("metric-simulation", passed ? canary.simulation_posts : "—");
  text("metric-broadcast", passed ? canary.broadcast_posts : "—");
  text("metric-funds", passed ? boolText(canary.funds_moved) : "—");
  text(
    "canary-level",
    passed ? "LIVE PROVIDER CANARY EVIDENCE" : "PROVIDER CANARY NOT LOADED"
  );
  text(
    "canary-status",
    passed
      ? canary.status
      : stopped
        ? "EVIDENCE LOAD STOPPED"
        : "WAITING FOR SANITIZED EVIDENCE"
  );

  const panel = byId("canary-panel");
  if (panel) {
    panel.className = passed
      ? "panel canary-panel"
      : "panel canary-panel is-unloaded";
  }

  const badge = byId("canary-badge");
  badge.className = passed
    ? "evidence-badge cyan"
    : stopped
      ? "evidence-badge red"
      : "evidence-badge";
  badge.textContent = passed
    ? `${canary.status} · NO BROADCAST`
    : stopped
      ? "STOP"
      : "NOT LOADED";

  setStage(
    "stage-simulate",
    passed ? "is-active" : "",
    "stage-simulate-status",
    passed
      ? "Independent canary evidence"
      : stopped
        ? "Evidence load stopped"
        : "Evidence not loaded"
  );

  const proofSimulation = byId("proof-simulation");
  if (proofSimulation) {
    proofSimulation.className = passed
      ? "proof-node cyan is-active"
      : "proof-node cyan";
  }
  text(
    "proof-simulation-title",
    passed
      ? "Independent provider canary simulated"
      : stopped
        ? "Canary evidence load stopped"
        : "Canary evidence not loaded"
  );
  text(
    "proof-simulation-copy",
    passed
      ? `${canary.amount} ${canary.asset} · no broadcast · no funds moved`
      : stopped
        ? "No provider evidence claim retained"
        : "No provider evidence claim yet"
  );

  if (!passed) {
    text(
      "canary-copy",
      stopped
        ? "Validated provider-canary evidence was rejected; inspect the read-only error banner."
        : "Start the console with a validated sanitized provider-canary evidence file."
    );
    text("provider-status", "—");
    text("provider-http", "—");
    text("provider-revert", "—");
    text("provider-gas", "—");
    text("action-sheet", "—");
    text("fingerprint", "—");
    text("claim-boundary", "NO PROVIDER CANARY EVIDENCE LOADED");
    return;
  }

  const provider = canary.provider_summary || {};
  text(
    "canary-copy",
    `Independent provider canary: ${canary.amount} ${canary.asset} on ${canary.chain}; simulation passed without transaction broadcast. This does not prove Anna + Mark Mission execution.`
  );
  text("provider-status", provider.provider_status);
  text("provider-http", provider.http_status);
  text("provider-revert", boolText(provider.would_revert));
  text("provider-gas", provider.gas_estimate);
  text("action-sheet", canary.action_sheet_binding);
  text("fingerprint", canary.request_fingerprint_binding);
  text(
    "claim-boundary",
    "PROVIDER CANARY ONLY · NOT MISSION EXECUTION OR TRANSACTION EVIDENCE"
  );
}

function renderMission(mission) {
  const loaded = mission.loaded === true;
  const stopped = mission.mission_state === "STOP";

  text("mission-ref", loaded ? mission.mission_ref : "—");
  text(
    "mission-state",
    loaded ? mission.mission_state : stopped ? "STOP" : "NOT LOADED"
  );
  text("mission-total", loaded ? `${mission.total_amount} USDC` : "—");

  if (loaded) {
    renderEffects(Array.isArray(mission.effects) ? mission.effects : []);
  } else {
    renderMissionPlaceholder(stopped);
  }

  const badge = byId("mission-badge");
  badge.className = loaded
    ? "evidence-badge blue"
    : stopped
      ? "evidence-badge red"
      : "evidence-badge";
  badge.textContent = loaded
    ? "OFFLINE PLAN"
    : stopped
      ? "PLAN STOP"
      : "PLAN NOT LOADED";

  setStage(
    "stage-plan",
    loaded ? "is-complete" : "",
    "stage-plan-status",
    loaded
      ? "Offline Mission plan evidence"
      : stopped
        ? "Plan load stopped"
        : "Plan not loaded"
  );

  const proofPlan = byId("proof-plan");
  if (proofPlan) {
    proofPlan.className = loaded
      ? "proof-node blue is-complete"
      : "proof-node blue";
  }
  text(
    "proof-plan-title",
    loaded
      ? "Offline Mission plan persisted and classified"
      : stopped
        ? "Mission evidence load stopped"
        : "Mission evidence not loaded"
  );
  text(
    "proof-plan-copy",
    loaded
      ? "Anna + Mark · zero provider calls"
      : stopped
        ? "Rejected local evidence is not treated as proof"
        : "Waiting for sanitized plan"
  );
  text(
    "continuation-status",
    loaded
      ? "CONTINUE ONLY THE MISSING EFFECT"
      : stopped
        ? "STOP · REVIEW SANITIZED PLAN"
        : "WAITING FOR SANITIZED PLAN"
  );
}

function renderErrors(errors) {
  const banner = byId("error-banner");
  if (!Array.isArray(errors) || errors.length === 0) {
    banner.hidden = true;
    text("error-text", "");
    return;
  }
  banner.hidden = false;
  text("error-text", errors.join(" · "));
}

function renderStoppedSnapshot(reason) {
  text("metric-mode", "READ ONLY");
  renderCanary({ loaded: false, status: "STOP" });
  renderMission({ loaded: false, mission_state: "STOP" });
  renderErrors([reason]);
}

async function refresh() {
  const button = byId("refresh");
  button.disabled = true;
  button.textContent = "Refreshing…";
  try {
    const response = await fetch("/api/runtime/snapshot", {
      method: "GET",
      cache: "no-store",
      credentials: "same-origin"
    });
    if (!response.ok) throw new Error(`HTTP_${response.status}`);
    const payload = await response.json();
    if (payload.schema !== "nexus-vector.operator-console.snapshot.v1") {
      throw new Error("UNSUPPORTED_SNAPSHOT");
    }
    text(
      "metric-mode",
      payload.browser_capabilities?.write_endpoints === false
        ? "READ ONLY"
        : "STOP"
    );
    renderCanary(payload.canary || {});
    renderMission(payload.mission || {});
    renderErrors(payload.errors || []);
  } catch (error) {
    renderStoppedSnapshot(`CONSOLE_REFRESH_FAILED:${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = "Refresh local state";
  }
}

byId("refresh").addEventListener("click", refresh);
refresh();
