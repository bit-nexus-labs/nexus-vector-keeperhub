# GitHub Pages Deployment Review

## Status

Prepared as a replacement draft pull request from Mission Control product main `dd7c5ab872c1bfb8e5ba29b767b41be1a918db0a`. No deployment occurs from the pull-request branch.

PR #26 is superseded because it was based on the pre-hardening, pre-Mission-Control product state. This replacement is built directly from current main and validates the unequal 12 + 7 + 11 replay, its public evidence hashes, and the exact frontend bytes intended for publication.

## Source artifact

Only the reviewed `frontend/` directory is uploaded. The workflow does not build, rewrite, minify, inject configuration into, or otherwise change UI bytes.

The uploaded directory contains:

- `frontend/index.html`;
- `frontend/styles.css`;
- `frontend/app.js`;
- `frontend/replay/mission-safe-30.js`;
- `frontend/.nojekyll`;
- public frontend documentation.

The interface remains `REPLAY / SANITIZED / NO LIVE TRANSACTION`. Its unsafe route is a counterfactual `42 / 30 projected` risk model; its safe route classifies Anna 12, Mark 7 and Leo 11 without claiming that funds moved.

## Validation before upload

- canonical tracked-repository hygiene verifier;
- public evidence verifier and exact artifact hashes;
- focused static replay tests;
- focused Mission Control claim, interaction and reduced-motion tests;
- Pages workflow path and deployment-boundary regression tests;
- Python 3.14 on `ubuntu-24.04`;
- exact-SHA-pinned GitHub-owned actions;
- checkout credentials are not persisted.

The repository's normal CI remains a separate pull-request gate on Python 3.12 and 3.14.

## Deployment boundary

The deploy job runs only when:

```text
github.ref == refs/heads/main
github.event_name != pull_request
```

A draft or ready pull request can validate and upload an ephemeral workflow artifact, but cannot run the deploy job.

Required deployment permissions are limited to:

```text
pages: write
id-token: write
```

The workflow-level default remains:

```text
contents: read
```

The workflow contains no KeeperHub, wallet, RPC, signing, transaction, credential, secret, or funds action.

## Manual merge and deployment gate

Before merge:

1. normal CI and Pages validation are green on the exact PR head;
2. exact workflow and three-file diff review passes;
3. the branch is based on current main or revalidated after any relevant frontend/evidence change;
4. repository Pages settings are confirmed to use **GitHub Actions** as the publishing source;
5. Olena explicitly approves the exact reviewed PR head for this deployment-triggering merge;
6. no unreviewed frontend bytes or submission claims are added.

Merge is deployment-triggering because the workflow runs on relevant pushes to `main`. A general code/merge instruction is therefore not treated as transaction-specific deployment approval.

## Expected public URL

```text
https://bit-nexus-labs.github.io/nexus-vector-keeperhub/
```

This is an expected location, not yet a verified submission link. Do not replace `PENDING_DEPLOYED_FRONTEND_URL` until:

1. the workflow reports successful deployment;
2. the URL opens in a clean/incognito browser;
3. Mission, Treasury Gate and Evidence views work;
4. Panic Retry shows only the labeled counterfactual and Safe Recovery shows 12 / 7 / 11;
5. the visible labels still state replay/sanitized/no-live-transaction;
6. no console errors, redirects, credential prompts, external runtime dependencies or horizontal overflow appear;
7. deployed frontend file digests match the reviewed repository artifacts;
8. the link is rechecked from the submission draft.

## Rollback

If deployment validation or clean-browser review fails:

- do not insert the URL into the submission;
- do not alter runtime or transaction claims;
- correct through a new reviewed pull request;
- preserve the current public evidence manifest until the deployed bytes are verified.
