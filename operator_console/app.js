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
    action.textContent = effect.continuation_action || "NO_ACTION";

    card.append(top, state, action);
    list.append(card);
  }
}

function renderCanary(canary) {
  const loaded = canary.loaded === true;
  text("metric-simulation", canary.simulation_posts ?? 0);
  text("metric-broadcast", canary.broadcast_posts ?? 0);
  text("metric-funds", boolText(canary.funds_moved));
  text(
    "canary-level",
    loaded ? "LIVE SIMULATION" : "LIVE SIMULATION NOT LOADED"
  );
  text("canary-status", canary.status || "WAITING FOR SANITIZED EVIDENCE");

  const badge = byId("canary-badge");
  badge.className =
    loaded && canary.status === "PASS"
      ? "evidence-badge cyan"
      : "evidence-badge red";
  badge.textContent = loaded
    ? `${canary.status} · NO BROADCAST`
    : "NOT LOADED";

  if (!loaded) {
    text(
      "canary-copy",
      "Start the console with a validated sanitized canary evidence file."
    );
    text("provider-status", "—");
    text("provider-http", "—");
    text("provider-revert", "—");
    text("provider-gas", "—");
    text("action-sheet", "—");
    text("fingerprint", "—");
    return;
  }

  const provider = canary.provider_summary || {};
  text(
    "canary-copy",
    `${canary.amount} ${canary.asset} on ${canary.chain}; simulation passed without transaction broadcast.`
  );
  text("provider-status", provider.provider_status);
  text("provider-http", provider.http_status);
  text("provider-revert", boolText(provider.would_revert));
  text("provider-gas", provider.gas_estimate);
  text("action-sheet", canary.action_sheet_binding);
  text("fingerprint", canary.request_fingerprint_binding);
  text("claim-boundary", "SIMULATION ONLY · NOT TRANSACTION EVIDENCE");
}

function renderMission(mission) {
  const loaded = mission.loaded === true;
  text("mission-ref", mission.mission_ref || "runtime-evidence-001");
  text("mission-state", mission.mission_state || "NOT_LOADED");
  text("mission-total", `${mission.total_amount || "0.19"} USDC`);
  renderEffects(Array.isArray(mission.effects) ? mission.effects : []);

  const badge = byId("mission-badge");
  badge.className = loaded ? "evidence-badge blue" : "evidence-badge red";
  badge.textContent = loaded ? "OFFLINE PLAN" : "PLAN NOT LOADED";
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
    renderErrors([`CONSOLE_REFRESH_FAILED:${error.message}`]);
  } finally {
    button.disabled = false;
    button.textContent = "Refresh local state";
  }
}

byId("refresh").addEventListener("click", refresh);
refresh();
