"use strict";

(function startMissionControl(document, manifest) {
  if (!manifest || manifest.mode !== "REPLAY" || manifest.liveTransaction !== false) {
    throw new Error("SAFE_REPLAY_MANIFEST_REQUIRED");
  }

  const byId = (id) => {
    const element = document.getElementById(id);
    if (!element) throw new Error(`MISSING_ELEMENT:${id}`);
    return element;
  };
  const text = (element, value) => { element.textContent = String(value); };

  const effectByRef = new Map(manifest.effects.map((effect) => [effect.effectRef, effect]));
  if (effectByRef.size !== manifest.effects.length) throw new Error("DUPLICATE_EFFECT_REF");

  const tabs = Array.from(document.querySelectorAll(".tab"));
  const panels = Array.from(document.querySelectorAll(".view-panel"));
  const presetButtons = Array.from(document.querySelectorAll("#incident-presets button"));
  const previousButton = byId("previous-step");
  const nextButton = byId("next-step");
  const timeline = byId("timeline");
  const panicButton = byId("panic-retry");
  const safeButton = byId("safe-recovery");
  const blackBoxButton = byId("open-black-box");
  let currentStepIndex = manifest.steps.length - 1;

  function setActiveView(viewName) {
    tabs.forEach((tab) => {
      const active = tab.dataset.view === viewName;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    panels.forEach((panel) => panel.classList.toggle("is-active", panel.id === `view-${viewName}`));
  }

  function statusClass(action) {
    if (["SKIP_VERIFIED", "VERIFIED", "CHAIN_CONFIRMED", "COMPLETE"].includes(action)) return "status-safe";
    if (["EXECUTE_MISSING", "PLANNED", "NONE", "PREPARED"].includes(action)) return "status-ready";
    if (["RECONCILE", "RECONCILE_REQUIRED", "EXECUTION_UNKNOWN", "IN_FLIGHT", "RESERVED", "UNRESOLVED"].includes(action)) return "status-warning";
    return "status-danger";
  }

  function makeEffectCard(effect, decision) {
    const article = document.createElement("article");
    article.className = "effect-card";

    const head = document.createElement("div");
    head.className = "effect-head";
    const heading = document.createElement("h3");
    text(heading, effect.label);
    const amount = document.createElement("span");
    amount.className = "effect-amount";
    text(amount, `${effect.amountBaseUnits} demo units`);
    head.append(heading, amount);

    const list = document.createElement("dl");
    [
      ["Effect state", decision.effectState],
      ["Attempt", decision.attemptState],
      ["Decision", decision.continuation],
      ["Doctor", decision.doctorCode]
    ].forEach(([label, value]) => {
      const row = document.createElement("div");
      const term = document.createElement("dt");
      const detail = document.createElement("dd");
      text(term, label);
      text(detail, value);
      detail.className = statusClass(value);
      row.append(term, detail);
      list.append(row);
    });

    article.append(head, list);
    return article;
  }

  function renderTechnicalTable(step) {
    const body = byId("technical-table");
    body.replaceChildren();
    manifest.effects.forEach((effect) => {
      const decision = step.effects[effect.effectRef];
      if (!decision) throw new Error(`MISSING_DECISION:${effect.effectRef}`);
      const row = document.createElement("tr");
      [effect.label, effect.amountBaseUnits, decision.effectState, decision.attemptState, decision.continuation, decision.doctorCode]
        .forEach((value, index) => {
          const cell = document.createElement("td");
          text(cell, value);
          if (index >= 2) cell.className = statusClass(value);
          row.append(cell);
        });
      body.append(row);
    });
  }

  function renderMissionState(state) {
    text(byId("technical-mission-state"), state);
    const order = ["RECEIVED", "VALIDATED", "PERSISTED", "RECONCILING", "READY_FOR_EXECUTION", "COMPLETED"];
    const effectiveState = state === "EXECUTING" || state === "EXECUTION_UNKNOWN" ? "RECONCILING" : state;
    const reachedIndex = Math.max(order.indexOf(effectiveState), 0);
    document.querySelectorAll(".state-track span").forEach((node, index) => {
      node.classList.toggle("is-reached", index <= reachedIndex);
      node.classList.toggle("current", node.dataset.state === effectiveState);
    });
  }

  function renderTreasuryGate(step) {
    const decisions = manifest.effects.map((effect) => step.effects[effect.effectRef]);
    const skipped = decisions.filter((decision) => decision.continuation === "SKIP_VERIFIED").length;
    const executable = decisions.filter((decision) => decision.continuation === "EXECUTE_MISSING").length;
    const reconcile = decisions.filter((decision) => decision.continuation === "RECONCILE_REQUIRED").length;
    const manual = decisions.filter((decision) => decision.continuation === "MANUAL_REVIEW").length;

    text(byId("gate-identity"), `${manifest.effects.length} canonical effects recognized`);
    text(byId("gate-budget"), `${step.amounts.skipped + step.amounts.executable + step.amounts.unresolved} / ${manifest.mission.totalAmountBaseUnits} classified`);
    text(byId("gate-attempt"), step.missionState === "PERSISTED" ? "no provider boundary entered" : "durable state precedes continuation");
    text(byId("gate-evidence"), step.amounts.unresolved > 0 ? `${step.amounts.unresolved} units require reconciliation` : "no unresolved amount remains");
    text(byId("gate-continuation"), manual > 0 ? "manual review blocks authority" : "duplicate authorization denied");
    text(byId("treasury-decision"), `${skipped} SKIP · ${executable} EXECUTE · ${reconcile} RECONCILE${manual ? ` · ${manual} REVIEW` : ""}`);
  }

  function renderStep(index) {
    currentStepIndex = Math.max(0, Math.min(index, manifest.steps.length - 1));
    const step = manifest.steps[currentStepIndex];
    text(byId("step-counter"), `Step ${currentStepIndex + 1} of ${manifest.steps.length} · ${step.title}`);
    previousButton.disabled = currentStepIndex === 0;
    nextButton.disabled = currentStepIndex === manifest.steps.length - 1;

    Array.from(timeline.children).forEach((node, position) => {
      node.classList.toggle("is-complete", position < currentStepIndex);
      node.classList.toggle("is-current", position === currentStepIndex);
    });
    presetButtons.forEach((button) => {
      button.classList.toggle("is-active", Number(button.dataset.step) === currentStepIndex);
    });

    text(byId("mission-total"), manifest.mission.totalAmountBaseUnits);
    text(byId("amount-skipped"), step.amounts.skipped);
    text(byId("amount-executable"), step.amounts.executable);
    text(byId("amount-unresolved"), step.amounts.unresolved);
    text(byId("doctor-title"), `Safe next action: ${step.doctorAction}`);
    text(byId("doctor-summary"), step.doctorSummary);
    const action = byId("doctor-action");
    text(action, step.doctorAction);
    action.dataset.action = step.doctorAction;

    const cards = byId("effect-cards");
    cards.replaceChildren();
    manifest.effects.forEach((effect) => {
      const decision = step.effects[effect.effectRef];
      if (!decision) throw new Error(`MISSING_DECISION:${effect.effectRef}`);
      cards.append(makeEffectCard(effect, decision));
    });

    renderTechnicalTable(step);
    renderMissionState(step.missionState);
    renderTreasuryGate(step);
  }

  function renderEvidence() {
    text(byId("manifest-mode"), manifest.mode);
    text(byId("manifest-schema"), manifest.schema);
    text(byId("manifest-hash"), manifest.manifestSha256);
    const list = byId("evidence-list");
    list.replaceChildren();
    manifest.evidence.forEach((record) => {
      const article = document.createElement("article");
      article.className = "evidence-record";
      const header = document.createElement("header");
      const heading = document.createElement("h3");
      const status = document.createElement("span");
      text(heading, `${record.effectRef} · ${record.kind}`);
      text(status, record.status);
      status.className = statusClass(record.status);
      header.append(heading, status);
      const summary = document.createElement("p");
      text(summary, record.summary);
      const fingerprint = document.createElement("code");
      text(fingerprint, record.fingerprint);
      article.append(header, summary, fingerprint);
      list.append(article);
    });
  }

  function setDecision(decision) {
    const stage = byId("decision-stage");
    stage.dataset.decision = decision;
    document.body.classList.toggle("decision-unsafe", decision === "unsafe");
    document.body.classList.toggle("decision-safe", decision === "safe");
    panicButton.setAttribute("aria-pressed", decision === "unsafe" ? "true" : "false");
    safeButton.setAttribute("aria-pressed", decision === "safe" ? "true" : "false");

    if (decision === "unsafe") {
      text(byId("decision-kicker"), "Counterfactual — not an executed transaction");
      text(byId("decision-title"), "Blind resend creates duplicate payout exposure");
      text(byId("decision-status"), "RISK PROJECTED");
      text(byId("decision-summary"), "The agent treats a missing response as proof of failure and resends the same economic effect. This path is a risk model only; Nexus Vector does not execute it.");
      text(byId("decision-budget"), "40 / 30 projected");
      text(byId("decision-duplicate"), "1 duplicate payout risk");
      text(byId("decision-route"), "blind resend");
      return;
    }

    text(byId("decision-kicker"), "Nexus recovery protocol — curated replay");
    text(byId("decision-title"), "Reconcile first. Continue only what is missing.");
    text(byId("decision-status"), "SAFE ROUTE");
    text(byId("decision-summary"), "One effect is verified and permanently skipped, one remains missing and eligible only after policy gates, and one stays unresolved for reconciliation.");
    text(byId("decision-budget"), "30 / 30 classified");
    text(byId("decision-duplicate"), "0 duplicate authorizations");
    text(byId("decision-route"), "skip · execute · reconcile");
    renderStep(manifest.steps.length - 1);
  }

  tabs.forEach((tab) => tab.addEventListener("click", () => setActiveView(tab.dataset.view)));
  previousButton.addEventListener("click", () => renderStep(currentStepIndex - 1));
  nextButton.addEventListener("click", () => renderStep(currentStepIndex + 1));
  presetButtons.forEach((button) => button.addEventListener("click", () => {
    setDecision("safe");
    renderStep(Number(button.dataset.step));
    setActiveView("simple");
  }));
  panicButton.addEventListener("click", () => setDecision("unsafe"));
  safeButton.addEventListener("click", () => setDecision("safe"));
  blackBoxButton.addEventListener("click", () => {
    setDecision("safe");
    setActiveView("simple");
    renderStep(2);
    byId("replay-heading").scrollIntoView({ behavior: "smooth", block: "center" });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft") renderStep(currentStepIndex - 1);
    if (event.key === "ArrowRight") renderStep(currentStepIndex + 1);
  });

  manifest.steps.forEach((step, index) => {
    const marker = document.createElement("span");
    marker.className = "timeline-step";
    marker.title = `${index + 1}. ${step.title}`;
    timeline.append(marker);
  });

  renderEvidence();
  renderStep(currentStepIndex);
})(document, window.NEXUS_REPLAY_MANIFEST);
