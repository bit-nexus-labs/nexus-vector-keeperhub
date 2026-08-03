"use strict";

(function maintainPresentationConsistency(document) {
  const byId = (id) => {
    const element = document.getElementById(id);
    if (!element) throw new Error(`MISSING_PRESENTATION_ELEMENT:${id}`);
    return element;
  };
  const text = (element, value) => { element.textContent = String(value); };

  const observedIds = [
    "mission-lock-status",
    "incident-status",
    "gate-evidence",
    "mission-total"
  ];

  function sync() {
    const missionLocked = byId("mission-lock-status").textContent.trim() === "PERSISTED";
    const scenarioActive = byId("incident-status").textContent.trim() !== "STANDBY";
    const missionTotal = byId("mission-total").textContent.trim();

    text(byId("mutation-policy"), missionLocked ? "new version required" : "editable until persist");
    text(byId("mission-total-caption"), missionLocked ? "immutable demo units" : "configured demo units");
    text(byId("executable-label"), missionLocked ? "Missing / eligible" : "Draft / awaiting persist");
    text(byId("executable-caption"), missionLocked ? "policy-gated continuation" : "persist before eligibility");

    const unsafeCard = byId("unsafe-outcome-card");
    unsafeCard.classList.toggle("is-neutral", !scenarioActive);

    if (!scenarioActive) {
      text(byId("unsafe-total"), "NOT EVALUATED");
      text(
        byId("unsafe-detail"),
        missionLocked ? "Select a failure scenario" : "Persist the Mission, then select a failure scenario"
      );
    }

    if (!missionLocked) {
      text(byId("safe-total"), "PERSIST FIRST");
      text(byId("safe-detail"), `${missionTotal} awaiting persist · 0 execution authority`);
    }

    const evidenceText = byId("gate-evidence").textContent.trim().toLowerCase();
    const evidenceWarning = evidenceText.includes("require") || evidenceText.includes("blocked");
    byId("gate-evidence-card").classList.toggle("gate-caution", evidenceWarning);
    text(byId("gate-evidence-icon"), evidenceWarning ? "!" : "✓");
  }

  const observer = new MutationObserver(sync);
  observedIds.forEach((id) => {
    observer.observe(byId(id), { childList: true, characterData: true, subtree: true });
  });

  sync();
})(document);
