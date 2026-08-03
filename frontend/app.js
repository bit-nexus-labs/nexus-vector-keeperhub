"use strict";

(function startMissionResilienceLab(document, manifest) {
  if (!manifest || manifest.mode !== "REPLAY" || manifest.liveTransaction !== false) {
    throw new Error("SAFE_REPLAY_MANIFEST_REQUIRED");
  }

  const MAX_EFFECTS = 10;
  const MIN_EFFECTS = 1;
  const byId = (id) => {
    const element = document.getElementById(id);
    if (!element) throw new Error(`MISSING_ELEMENT:${id}`);
    return element;
  };
  const text = (element, value) => { element.textContent = String(value); };
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const presets = {
    single: [{ alias: "Recipient A", amount: 5 }],
    unequal: [
      { alias: "Anna", amount: 12 },
      { alias: "Mark", amount: 7 },
      { alias: "Leo", amount: 11 }
    ],
    batch: [
      { alias: "Ops", amount: 8 },
      { alias: "Research", amount: 5 },
      { alias: "Design", amount: 11 },
      { alias: "QA", amount: 6 }
    ],
    mixed: [
      { alias: "Verified", amount: 9 },
      { alias: "Missing", amount: 4 },
      { alias: "Unknown", amount: 13 },
      { alias: "Review", amount: 3 },
      { alias: "Reserve", amount: 7 }
    ]
  };

  const scenarioCopy = {
    "lost-response": {
      title: "Provider response lost after one effect crossed the boundary",
      summary: "The outcome remains EXECUTION_UNKNOWN. A new request key is forbidden until the original effect is reconciled.",
      status: "RECONCILE",
      severity: "warning"
    },
    "double-submit": {
      title: "Concurrent duplicate request suppressed",
      summary: "One canonical writer owns the effect. The duplicate path receives no independent economic authority.",
      status: "DUPLICATE DENIED",
      severity: "warning"
    },
    restart: {
      title: "Process restarted with durable state intact",
      summary: "The new process epoch restores Mission identity, attempts and provider-reference state before calculating continuation.",
      status: "RECOVERED",
      severity: "warning"
    },
    "payload-mutation": {
      title: "Payload mutation conflicts with the canonical effect",
      summary: "The changed amount cannot reuse an existing effect identity. Continuation is blocked for manual review.",
      status: "CONFLICT",
      severity: "danger"
    },
    "retry-all": {
      title: "Full-Mission retry reduced to per-effect continuation",
      summary: "Verified work is skipped, unresolved work is reconciled, and only missing effects remain eligible.",
      status: "PARTITIONED",
      severity: "warning"
    }
  };

  let payments = clone(presets.unequal);
  let missionLocked = false;
  let activeScenario = null;
  let processEpoch = 1;
  let sessionCounter = 1;
  let flightEvents = [];
  let decisions = [];

  const paymentList = byId("payment-list");
  const addPaymentButton = byId("add-payment");
  const lockMissionButton = byId("lock-mission");
  const resetButton = byId("reset-session");
  const scenarioButtons = Array.from(document.querySelectorAll(".scenario-card"));
  const presetButtons = Array.from(document.querySelectorAll(".preset-button"));
  const tabs = Array.from(document.querySelectorAll(".tab"));
  const panels = Array.from(document.querySelectorAll(".view-panel"));

  function amountTotal() {
    return payments.reduce((sum, payment) => sum + Math.max(0, Number(payment.amount) || 0), 0);
  }

  function sanitizedAlias(value, index) {
    const trimmed = String(value || "").trim().replace(/\s+/g, " ");
    return trimmed.slice(0, 32) || `Recipient ${index + 1}`;
  }

  function deterministicFingerprint() {
    const canonical = payments.map((payment, index) => (
      `${index + 1}:${sanitizedAlias(payment.alias, index).toLowerCase()}:${Math.max(0, Number(payment.amount) || 0)}`
    )).join("|");
    let hash = 2166136261;
    for (let index = 0; index < canonical.length; index += 1) {
      hash ^= canonical.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return `lab-checksum:${(hash >>> 0).toString(16).padStart(8, "0")}:${payments.length}`;
  }

  function statusClass(value) {
    if (["SKIP_VERIFIED", "VERIFIED", "CHAIN_CONFIRMED", "COMPLETE", "DUPLICATE_SUPPRESSED"].includes(value)) return "status-safe";
    if (["EXECUTE_MISSING", "PLANNED", "NONE", "PREPARED", "ELIGIBLE"].includes(value)) return "status-ready";
    if (["RECONCILE", "RECONCILE_REQUIRED", "EXECUTION_UNKNOWN", "IN_FLIGHT", "UNRESOLVED"].includes(value)) return "status-warning";
    return "status-danger";
  }

  function makeDecision(effectIndex) {
    const base = {
      effectState: missionLocked ? "MISSING" : "PLANNED",
      attemptState: missionLocked ? "PREPARED" : "NONE",
      continuation: missionLocked ? "EXECUTE_MISSING" : "PERSIST_FIRST",
      doctorCode: missionLocked ? "ELIGIBLE" : "MISSION_NOT_PERSISTED"
    };

    if (!missionLocked || !activeScenario) return base;

    if (activeScenario === "lost-response") {
      if (effectIndex === 0) return {
        effectState: "UNRESOLVED",
        attemptState: "EXECUTION_UNKNOWN",
        continuation: "RECONCILE_REQUIRED",
        doctorCode: "RESPONSE_LOST"
      };
      return base;
    }

    if (activeScenario === "double-submit") {
      if (effectIndex === 0) return {
        effectState: "RESERVED",
        attemptState: "IN_FLIGHT",
        continuation: "RECONCILE_REQUIRED",
        doctorCode: "DUPLICATE_SUPPRESSED"
      };
      return base;
    }

    if (activeScenario === "restart") {
      if (effectIndex === 0) return {
        effectState: "CHAIN_CONFIRMED",
        attemptState: "VERIFIED",
        continuation: "SKIP_VERIFIED",
        doctorCode: "RESTORED_FROM_DURABLE_STATE"
      };
      if (effectIndex === 1) return {
        effectState: "UNRESOLVED",
        attemptState: "EXECUTION_UNKNOWN",
        continuation: "RECONCILE_REQUIRED",
        doctorCode: "PROVIDER_REFERENCE_CHECK"
      };
      return base;
    }

    if (activeScenario === "payload-mutation") {
      if (effectIndex === 0) return {
        effectState: "CONFLICT",
        attemptState: "BLOCKED",
        continuation: "MANUAL_REVIEW",
        doctorCode: "FINGERPRINT_MISMATCH"
      };
      return base;
    }

    if (activeScenario === "retry-all") {
      if (effectIndex === 0) return {
        effectState: "CHAIN_CONFIRMED",
        attemptState: "VERIFIED",
        continuation: "SKIP_VERIFIED",
        doctorCode: "ALREADY_VERIFIED"
      };
      if (effectIndex === 1 && payments.length > 2) return {
        effectState: "UNRESOLVED",
        attemptState: "EXECUTION_UNKNOWN",
        continuation: "RECONCILE_REQUIRED",
        doctorCode: "OUTCOME_AMBIGUOUS"
      };
      return base;
    }

    return base;
  }

  function rebuildDecisions() {
    decisions = payments.map((_, index) => makeDecision(index));
  }

  function partition() {
    return payments.reduce((accumulator, payment, index) => {
      const amount = Math.max(0, Number(payment.amount) || 0);
      const decision = decisions[index];
      if (decision.continuation === "SKIP_VERIFIED") accumulator.skipped += amount;
      else if (decision.continuation === "RECONCILE_REQUIRED") accumulator.unresolved += amount;
      else if (decision.continuation === "MANUAL_REVIEW") accumulator.review += amount;
      else accumulator.executable += amount;
      return accumulator;
    }, { skipped: 0, executable: 0, unresolved: 0, review: 0 });
  }

  function event(code, summary, tone = "neutral") {
    flightEvents.push({
      time: `${String(flightEvents.length).padStart(2, "0")}:${String((flightEvents.length * 17) % 60).padStart(2, "0")}.${String((flightEvents.length * 137) % 1000).padStart(3, "0")}`,
      code,
      summary,
      tone
    });
  }

  function resetEvents() {
    flightEvents = [];
    event("SESSION_CREATED", `${payments.length} draft effects loaded`);
  }

  function renderPaymentRows() {
    paymentList.replaceChildren();
    payments.forEach((payment, index) => {
      const row = document.createElement("div");
      row.className = "payment-row";

      const number = document.createElement("span");
      number.className = "effect-number";
      text(number, `E${String(index + 1).padStart(2, "0")}`);

      const aliasLabel = document.createElement("label");
      const aliasInput = document.createElement("input");
      aliasInput.type = "text";
      aliasInput.maxLength = 32;
      aliasInput.value = payment.alias;
      aliasInput.disabled = missionLocked;
      aliasInput.setAttribute("aria-label", `Recipient alias for effect ${index + 1}`);
      aliasInput.addEventListener("input", () => {
        payments[index].alias = sanitizedAlias(aliasInput.value, index);
        markDraftAfterMutation();
      });
      aliasLabel.append(aliasInput);

      const amountLabel = document.createElement("label");
      const amountInput = document.createElement("input");
      amountInput.type = "number";
      amountInput.min = "1";
      amountInput.max = "999";
      amountInput.step = "1";
      amountInput.inputMode = "numeric";
      amountInput.value = String(payment.amount);
      amountInput.disabled = missionLocked;
      amountInput.setAttribute("aria-label", `Demo units for effect ${index + 1}`);
      amountInput.addEventListener("input", () => {
        const parsed = Number.parseInt(amountInput.value, 10);
        payments[index].amount = Number.isFinite(parsed) ? Math.max(1, Math.min(999, parsed)) : 1;
        amountInput.setAttribute("aria-invalid", Number.isFinite(parsed) && parsed > 0 ? "false" : "true");
        markDraftAfterMutation();
      });
      amountLabel.append(amountInput);

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "remove-payment";
      remove.disabled = missionLocked || payments.length <= MIN_EFFECTS;
      remove.setAttribute("aria-label", `Remove effect ${index + 1}`);
      text(remove, "Remove");
      remove.addEventListener("click", () => {
        if (missionLocked || payments.length <= MIN_EFFECTS) return;
        payments.splice(index, 1);
        markDraftAfterMutation();
        render();
      });

      row.append(number, aliasLabel, amountLabel, remove);
      paymentList.append(row);
    });
  }

  function markDraftAfterMutation() {
    if (missionLocked) return;
    activeScenario = null;
    rebuildDecisions();
    renderSummaries();
  }

  function renderEffectCards() {
    const cards = byId("effect-cards");
    const body = byId("technical-table");
    cards.replaceChildren();
    body.replaceChildren();

    payments.forEach((payment, index) => {
      const decision = decisions[index];
      const article = document.createElement("article");
      article.className = "effect-card";

      const head = document.createElement("div");
      head.className = "effect-head";
      const heading = document.createElement("h3");
      text(heading, `${sanitizedAlias(payment.alias, index)} · E${String(index + 1).padStart(2, "0")}`);
      const amount = document.createElement("span");
      amount.className = "effect-amount";
      text(amount, `${payment.amount} demo units`);
      head.append(heading, amount);

      const list = document.createElement("dl");
      [
        ["Effect state", decision.effectState],
        ["Attempt", decision.attemptState],
        ["Continuation", decision.continuation],
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
      cards.append(article);

      const tableRow = document.createElement("tr");
      [
        `E${String(index + 1).padStart(2, "0")}`,
        sanitizedAlias(payment.alias, index),
        payment.amount,
        decision.effectState,
        decision.attemptState,
        decision.continuation
      ].forEach((value, cellIndex) => {
        const cell = document.createElement("td");
        text(cell, value);
        if (cellIndex >= 3) cell.className = statusClass(value);
        tableRow.append(cell);
      });
      body.append(tableRow);
    });
  }

  function renderFlightRecorder() {
    const recorder = byId("flight-recorder");
    recorder.replaceChildren();
    flightEvents.slice(-7).forEach((record) => {
      const row = document.createElement("div");
      row.className = `flight-event ${record.tone}`;
      const timeNode = document.createElement("time");
      text(timeNode, record.time);
      const copy = document.createElement("div");
      const strong = document.createElement("strong");
      const small = document.createElement("small");
      text(strong, record.code);
      text(small, record.summary);
      copy.append(strong, small);
      row.append(timeNode, copy);
      recorder.append(row);
    });
  }

  function renderMissionState(state) {
    text(byId("technical-mission-state"), state);
    const order = ["RECEIVED", "VALIDATED", "PERSISTED", "RECONCILING", "READY_FOR_EXECUTION", "COMPLETED"];
    const reached = Math.max(0, order.indexOf(state));
    document.querySelectorAll(".state-track span").forEach((node, index) => {
      node.classList.toggle("is-reached", index <= reached);
      node.classList.toggle("current", node.dataset.state === state);
    });
  }

  function currentMissionState() {
    if (!missionLocked) return "RECEIVED";
    if (!activeScenario) return "PERSISTED";
    if (activeScenario === "payload-mutation") return "RECONCILING";
    if (decisions.some((decision) => decision.continuation === "RECONCILE_REQUIRED")) return "RECONCILING";
    return "READY_FOR_EXECUTION";
  }

  function duplicateProjection() {
    const total = amountTotal();
    if (!activeScenario || !payments.length) return { total, riskAmount: 0, label: "No failure selected" };
    if (activeScenario === "payload-mutation") return { total, riskAmount: 0, label: "Mutation blocked before authority" };
    const riskyIndex = activeScenario === "restart" && payments.length > 1 ? 1 : 0;
    const riskAmount = Math.max(0, Number(payments[riskyIndex].amount) || 0);
    return { total: total + riskAmount, riskAmount, label: `Projects one repeated ${riskAmount}-unit effect` };
  }

  function providerMetrics() {
    if (!activeScenario) return { requests: 0, executions: 0, duplicates: 0 };
    if (activeScenario === "double-submit") return { requests: 2, executions: 1, duplicates: 0 };
    if (activeScenario === "retry-all") return { requests: Math.max(2, payments.length + 1), executions: 1, duplicates: 0 };
    if (activeScenario === "payload-mutation") return { requests: 1, executions: 0, duplicates: 0 };
    return { requests: 1, executions: 1, duplicates: 0 };
  }

  function doctor() {
    if (!missionLocked) return {
      action: "PERSIST",
      title: "Safe next action: PERSIST MISSION",
      summary: "Persist the Mission before any effect can cross a provider boundary."
    };
    if (!activeScenario) return {
      action: "READY",
      title: "Safe next action: SELECT SCENARIO",
      summary: "The Mission is durable. Apply a failure scenario to inspect safe continuation."
    };
    if (decisions.some((decision) => decision.continuation === "MANUAL_REVIEW")) return {
      action: "BLOCK",
      title: "Safe next action: BLOCK AND REVIEW",
      summary: "A fingerprint conflict prevents continued execution under the existing effect identity."
    };
    if (decisions.some((decision) => decision.continuation === "RECONCILE_REQUIRED")) return {
      action: "RECONCILE",
      title: "Safe next action: RECONCILE",
      summary: "At least one outcome is ambiguous. Verify the original economic effect before any new authority."
    };
    return {
      action: "CONTINUE",
      title: "Safe next action: EXECUTE MISSING ONLY",
      summary: "Verified effects remain permanently skipped. Only canonical missing effects remain eligible."
    };
  }

  function treasurySummary(parts) {
    const skipped = decisions.filter((decision) => decision.continuation === "SKIP_VERIFIED").length;
    const executable = decisions.filter((decision) => decision.continuation === "EXECUTE_MISSING").length;
    const reconcile = decisions.filter((decision) => decision.continuation === "RECONCILE_REQUIRED").length;
    const review = decisions.filter((decision) => decision.continuation === "MANUAL_REVIEW").length;
    if (!missionLocked) return "PERSIST MISSION BEFORE EXECUTION";
    return `${skipped} SKIP · ${executable} EXECUTE · ${reconcile} RECONCILE${review ? ` · ${review} REVIEW` : ""}`;
  }

  function renderSummaries() {
    rebuildDecisions();
    const total = amountTotal();
    const parts = partition();
    const projection = duplicateProjection();
    const metrics = providerMetrics();
    const doctorState = doctor();
    const missionState = currentMissionState();

    text(byId("builder-effect-count"), payments.length);
    text(byId("builder-total"), total);
    text(byId("telemetry-total"), `${total} demo units`);
    text(byId("telemetry-effects"), payments.length);
    text(byId("telemetry-state"), missionLocked ? missionState : "MISSION_DRAFT");
    text(byId("mission-total"), total);
    text(byId("amount-skipped"), parts.skipped);
    text(byId("amount-executable"), parts.executable);
    text(byId("amount-unresolved"), parts.unresolved + parts.review);
    text(byId("mission-fingerprint"), missionLocked ? deterministicFingerprint() : "draft");
    text(byId("budget-invariant"), `${parts.skipped + parts.executable + parts.unresolved + parts.review} / ${total} balanced`);
    text(byId("unsafe-total"), `${projection.total} / ${total}`);
    text(byId("unsafe-detail"), projection.label);
    text(byId("safe-total"), `${total} / ${total}`);
    text(byId("safe-detail"), parts.review > 0
      ? `${parts.review} units blocked for review`
      : `${parts.skipped} skip · ${parts.executable} eligible · ${parts.unresolved} reconcile`);
    text(byId("doctor-title"), doctorState.title);
    text(byId("doctor-summary"), doctorState.summary);
    text(byId("doctor-action"), doctorState.action);
    text(byId("process-epoch"), processEpoch);
    text(byId("provider-request-count"), metrics.requests);
    text(byId("unique-execution-count"), metrics.executions);
    text(byId("duplicate-effect-count"), metrics.duplicates);
    text(byId("mission-lock-status"), missionLocked ? "PERSISTED" : "DRAFT");
    text(byId("gate-identity"), missionLocked ? `${payments.length} canonical effects persisted` : `${payments.length} draft effects recognized`);
    text(byId("gate-budget"), `${total} / ${total} partition invariant`);
    text(byId("gate-attempt"), activeScenario ? "durable state precedes provider boundary" : "no provider boundary entered");
    text(byId("gate-evidence"), parts.unresolved > 0 ? `${parts.unresolved} units require reconciliation` : parts.review > 0 ? `${parts.review} units blocked for review` : "no unresolved evidence");
    text(byId("gate-continuation"), "duplicate economic authority denied");
    text(byId("treasury-decision"), treasurySummary(parts));
    renderMissionState(missionState);
    renderEffectCards();
    renderFlightRecorder();

    addPaymentButton.disabled = missionLocked || payments.length >= MAX_EFFECTS;
    lockMissionButton.disabled = missionLocked;
    lockMissionButton.textContent = missionLocked ? "Mission persisted" : "Persist Mission";
    scenarioButtons.forEach((button) => { button.disabled = !missionLocked; });
  }

  function renderIncident() {
    const signal = byId("signal-state");
    scenarioButtons.forEach((button) => button.classList.toggle("is-active", button.dataset.scenario === activeScenario));
    if (!activeScenario) {
      text(byId("incident-kicker"), missionLocked ? "Mission persisted" : "No scenario applied");
      text(byId("incident-title"), missionLocked ? "Select a failure scenario" : "Persist the Mission, then select a failure scenario");
      text(byId("incident-summary"), "All controls are local. No provider request, wallet operation, signature or transaction is performed.");
      text(byId("incident-status"), "STANDBY");
      byId("incident-stage").dataset.severity = "neutral";
      signal.className = "signal-ready";
      const readyDot = document.createElement("i");
      readyDot.setAttribute("aria-hidden", "true");
      signal.replaceChildren(readyDot, document.createTextNode(" READY"));
      return;
    }
    const copy = scenarioCopy[activeScenario];
    text(byId("incident-kicker"), "Controlled failure applied");
    text(byId("incident-title"), copy.title);
    text(byId("incident-summary"), copy.summary);
    text(byId("incident-status"), copy.status);
    byId("incident-stage").dataset.severity = copy.severity;
    signal.className = "signal-lost";
    const incidentDot = document.createElement("i");
    incidentDot.setAttribute("aria-hidden", "true");
    signal.replaceChildren(incidentDot, document.createTextNode(" INCIDENT"));
  }

  function render() {
    renderPaymentRows();
    renderIncident();
    renderSummaries();
  }

  function setPreset(name) {
    if (missionLocked || !presets[name]) return;
    payments = clone(presets[name]);
    activeScenario = null;
    presetButtons.forEach((button) => button.classList.toggle("is-active", button.dataset.preset === name));
    resetEvents();
    render();
  }

  function lockMission() {
    if (missionLocked) return;
    if (payments.length < MIN_EFFECTS || payments.length > MAX_EFFECTS) throw new Error("INVALID_EFFECT_COUNT");
    payments = payments.map((payment, index) => ({
      alias: sanitizedAlias(payment.alias, index),
      amount: Math.max(1, Math.min(999, Number(payment.amount) || 1))
    }));
    missionLocked = true;
    activeScenario = null;
    event("MISSION_VALIDATED", `${payments.length} canonical effects · ${amountTotal()} demo units`);
    event("MISSION_PERSISTED", deterministicFingerprint(), "safe");
    render();
  }

  function applyScenario(name) {
    if (!missionLocked || !scenarioCopy[name]) return;
    activeScenario = name;
    if (name === "restart") processEpoch += 1;
    event("FAILURE_SCENARIO_APPLIED", name.toUpperCase().replaceAll("-", "_"), "alert");
    if (name === "lost-response") event("EXECUTION_UNKNOWN", "blind retry denied; reconciliation required", "alert");
    if (name === "double-submit") event("DUPLICATE_SUPPRESSED", "two simulated requests · one canonical authority", "safe");
    if (name === "restart") event("PROCESS_RESTARTED", `epoch ${processEpoch}; durable state restored`, "safe");
    if (name === "payload-mutation") event("FINGERPRINT_CONFLICT", "changed economic body blocked", "alert");
    if (name === "retry-all") event("MISSION_REPARTITIONED", "verified skip · missing execute · unknown reconcile", "safe");
    render();
  }

  function resetSession() {
    payments = clone(presets.unequal);
    missionLocked = false;
    activeScenario = null;
    processEpoch = 1;
    sessionCounter += 1;
    text(byId("session-id"), `NV-${String(sessionCounter).padStart(4, "0")}`);
    presetButtons.forEach((button) => button.classList.toggle("is-active", button.dataset.preset === "unequal"));
    resetEvents();
    render();
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

  function setActiveView(viewName) {
    tabs.forEach((tab) => {
      const active = tab.dataset.view === viewName;
      tab.classList.toggle("is-active", active);
      tab.setAttribute("aria-selected", active ? "true" : "false");
    });
    panels.forEach((panel) => panel.classList.toggle("is-active", panel.id === `view-${viewName}`));
  }

  presetButtons.forEach((button) => button.addEventListener("click", () => setPreset(button.dataset.preset)));
  addPaymentButton.addEventListener("click", () => {
    if (missionLocked || payments.length >= MAX_EFFECTS) return;
    payments.push({ alias: `Recipient ${payments.length + 1}`, amount: 1 });
    activeScenario = null;
    event("EFFECT_ADDED", `effect ${payments.length} added to draft`);
    render();
  });
  lockMissionButton.addEventListener("click", lockMission);
  resetButton.addEventListener("click", resetSession);
  scenarioButtons.forEach((button) => button.addEventListener("click", () => applyScenario(button.dataset.scenario)));
  byId("open-black-box").addEventListener("click", () => {
    event("BLACK_BOX_OPENED", "durable Mission, attempt and provider-reference layers inspected", "safe");
    renderFlightRecorder();
    byId("flight-recorder").scrollIntoView({
      behavior: prefersReducedMotion ? "auto" : "smooth",
      block: "center"
    });
  });
  tabs.forEach((tab) => tab.addEventListener("click", () => setActiveView(tab.dataset.view)));

  document.addEventListener("keydown", (eventObject) => {
    if (eventObject.altKey && eventObject.key === "1") setActiveView("mission");
    if (eventObject.altKey && eventObject.key === "2") setActiveView("treasury");
    if (eventObject.altKey && eventObject.key === "3") setActiveView("evidence");
  });

  resetEvents();
  renderEvidence();
  render();
})(document, window.NEXUS_REPLAY_MANIFEST);
