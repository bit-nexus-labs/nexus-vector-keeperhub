"use strict";

(function exposeReplayManifest(global) {
  const manifest = Object.freeze({
    schema: "nexus-vector.replay.v1",
    mode: "REPLAY",
    sanitized: true,
    liveTransaction: false,
    mission: Object.freeze({
      missionKey: "m_7e7c20d6e66d9cc8f1f4856e6a9cd609",
      missionRef: "demo-mission-safe-30",
      chainId: 84532,
      asset: "DEMO-ERC20",
      totalAmountBaseUnits: 30,
      sender: "0xDEMO_SENDER_REDACTED"
    }),
    effects: Object.freeze([
      Object.freeze({
        effectId: "e_32bff66081f111a5dd4a606f53c65a11",
        effectRef: "anna",
        label: "Anna",
        recipient: "0xANNA_REDACTED",
        amountBaseUnits: 12
      }),
      Object.freeze({
        effectId: "e_44aa90e97b8a20f5284b27e03dbbdb17",
        effectRef: "mark",
        label: "Mark",
        recipient: "0xMARK_REDACTED",
        amountBaseUnits: 7
      }),
      Object.freeze({
        effectId: "e_b745469247aab23df29106940f1fa1a4",
        effectRef: "leo",
        label: "Leo",
        recipient: "0xLEO_REDACTED",
        amountBaseUnits: 11
      })
    ]),
    steps: Object.freeze([
      Object.freeze({
        id: "durable-admission",
        title: "Mission persisted",
        missionState: "PERSISTED",
        doctorAction: "EXECUTE_MISSING",
        doctorSummary: "All three canonical effects are durable. This replay displays eligibility only; it does not execute anything.",
        amounts: Object.freeze({ skipped: 0, executable: 30, unresolved: 0 }),
        effects: Object.freeze({
          anna: Object.freeze({ effectState: "PLANNED", attemptState: "NONE", continuation: "EXECUTE_MISSING", doctorCode: "MISSING_EFFECT" }),
          mark: Object.freeze({ effectState: "PLANNED", attemptState: "NONE", continuation: "EXECUTE_MISSING", doctorCode: "MISSING_EFFECT" }),
          leo: Object.freeze({ effectState: "PLANNED", attemptState: "NONE", continuation: "EXECUTE_MISSING", doctorCode: "MISSING_EFFECT" })
        })
      }),
      Object.freeze({
        id: "anna-in-flight",
        title: "Attempt persisted first",
        missionState: "EXECUTING",
        doctorAction: "RECONCILE",
        doctorSummary: "Anna is IN_FLIGHT. The durable attempt blocks a second send until the first outcome is reconciled.",
        amounts: Object.freeze({ skipped: 0, executable: 18, unresolved: 12 }),
        effects: Object.freeze({
          anna: Object.freeze({ effectState: "RESERVED", attemptState: "IN_FLIGHT", continuation: "RECONCILE_REQUIRED", doctorCode: "POSSIBLE_EXECUTION" }),
          mark: Object.freeze({ effectState: "PLANNED", attemptState: "NONE", continuation: "EXECUTE_MISSING", doctorCode: "MISSING_EFFECT" }),
          leo: Object.freeze({ effectState: "PLANNED", attemptState: "NONE", continuation: "EXECUTE_MISSING", doctorCode: "MISSING_EFFECT" })
        })
      }),
      Object.freeze({
        id: "lost-response",
        title: "Response lost",
        missionState: "EXECUTION_UNKNOWN",
        doctorAction: "RECONCILE",
        doctorSummary: "The client did not receive a trustworthy result. UNKNOWN is durable and never treated as permission to resend.",
        amounts: Object.freeze({ skipped: 0, executable: 18, unresolved: 12 }),
        effects: Object.freeze({
          anna: Object.freeze({ effectState: "EXECUTION_UNKNOWN", attemptState: "EXECUTION_UNKNOWN", continuation: "RECONCILE_REQUIRED", doctorCode: "OUTCOME_UNKNOWN" }),
          mark: Object.freeze({ effectState: "PLANNED", attemptState: "NONE", continuation: "EXECUTE_MISSING", doctorCode: "MISSING_EFFECT" }),
          leo: Object.freeze({ effectState: "PLANNED", attemptState: "NONE", continuation: "EXECUTE_MISSING", doctorCode: "MISSING_EFFECT" })
        })
      }),
      Object.freeze({
        id: "restart-verification",
        title: "Restart + independent verification",
        missionState: "RECONCILING",
        doctorAction: "RECONCILE",
        doctorSummary: "After restart, exact chain evidence confirms Anna. Leo remains unresolved, so the Mission stays in reconciliation.",
        amounts: Object.freeze({ skipped: 12, executable: 7, unresolved: 11 }),
        effects: Object.freeze({
          anna: Object.freeze({ effectState: "CHAIN_CONFIRMED", attemptState: "VERIFIED", continuation: "SKIP_VERIFIED", doctorCode: "EXACT_TRANSFER_VERIFIED" }),
          mark: Object.freeze({ effectState: "PLANNED", attemptState: "NONE", continuation: "EXECUTE_MISSING", doctorCode: "MISSING_EFFECT" }),
          leo: Object.freeze({ effectState: "EXECUTION_UNKNOWN", attemptState: "EXECUTION_UNKNOWN", continuation: "RECONCILE_REQUIRED", doctorCode: "OUTCOME_UNKNOWN" })
        })
      }),
      Object.freeze({
        id: "safe-continuation",
        title: "Only missing work remains",
        missionState: "RECONCILING",
        doctorAction: "RECONCILE",
        doctorSummary: "Anna is permanently skipped. Mark is the only missing candidate. Leo must be reconciled before any final completion claim.",
        amounts: Object.freeze({ skipped: 12, executable: 7, unresolved: 11 }),
        effects: Object.freeze({
          anna: Object.freeze({ effectState: "CHAIN_CONFIRMED", attemptState: "VERIFIED", continuation: "SKIP_VERIFIED", doctorCode: "ALREADY_PAID" }),
          mark: Object.freeze({ effectState: "PLANNED", attemptState: "NONE", continuation: "EXECUTE_MISSING", doctorCode: "MISSING_EFFECT" }),
          leo: Object.freeze({ effectState: "EXECUTION_UNKNOWN", attemptState: "EXECUTION_UNKNOWN", continuation: "RECONCILE_REQUIRED", doctorCode: "RECONCILIATION_REQUIRED" })
        })
      })
    ]),
    evidence: Object.freeze([
      Object.freeze({
        evidenceRef: "ev_anna_exact_transfer",
        effectRef: "anna",
        kind: "SANITIZED_CHAIN_EVENT",
        status: "VERIFIED",
        summary: "Exact chain, token, sender, recipient and integer amount matched at the configured confirmation threshold.",
        fingerprint: "sha256:1d76881683e061c17c0944932712b0cbbc69bdc2fa221be187812a4cbae3cdb0"
      }),
      Object.freeze({
        evidenceRef: "ev_leo_unknown_outcome",
        effectRef: "leo",
        kind: "SANITIZED_RECOVERY_OBSERVATION",
        status: "UNRESOLVED",
        summary: "A durable attempt exists, but the curated observation does not prove success or final rejection. Blind retry remains blocked.",
        fingerprint: "sha256:d4e8ab9a3f2b4c5d6627677caf877fdedd42ea26e193a704f696fd1fc63ceaf6"
      })
    ]),
    manifestSha256: "sha256:e19c57a533bc0d56f8c0a1c244b71525d87d6da6b8f3acaa010e96730b80fc00"
  });

  Object.defineProperty(global, "NEXUS_REPLAY_MANIFEST", {
    value: manifest,
    enumerable: true,
    configurable: false,
    writable: false
  });
})(window);
