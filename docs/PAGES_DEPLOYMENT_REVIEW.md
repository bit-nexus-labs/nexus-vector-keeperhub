# GitHub Pages Deployment Review

## Status

Prepared as a replacement draft pull request from current product main `865fa04e1b6d670556a945662c2494fc9da9c178`. No deployment occurs from the pull-request branch.

The previous PR #21 is superseded because it was prepared from old main ancestry before the provider integration, runtime-readiness, and evidence updates were merged. Its workflow used the correct hygiene verifier, but its branch and review record no longer represented current product main. The replacement keeps the canonical path `tools/verify_repository_hygiene.py` and adds a regression test that requires every referenced validation file to exist.

## Source artifact

Only the reviewed `frontend/` directory is uploaded. The workflow does not build, rewrite, minify, inject configuration into, or otherwise change UI bytes.

The uploaded directory contains the curated artifact already covered by the public evidence manifest:

- `frontend/index.html`;
- `frontend/styles.css`;
- `frontend/app.js`;
- `frontend/replay/mission-safe-30.js`;
- `frontend/.nojekyll`;
- public frontend documentation.

The interface remains `REPLAY / SANITIZED / NO LIVE TRANSACTION`.

## Validation before upload

- compile validation for frontend-related tests and tools;
- canonical tracked-repository hygiene verifier;
- public evidence verifier and artifact hashes;
- focused static replay UI tests;
- Pages workflow path and deployment-boundary regression tests;
- Python 3.14 on `ubuntu-24.04`;
- exact-SHA-pinned GitHub-owned actions;
- checkout credentials are not persisted.

The repository's normal CI remains a separate required pull-request gate on Python 3.12 and 3.14.

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

The workflow contains no KeeperHub, wallet, RPC, signing, transaction, secret, or funds action.

## Manual merge and deployment gate

Before merge:

1. draft PR normal CI and Pages validation are green;
2. exact workflow/diff review passes;
3. the branch is based on current main or revalidated after any relevant frontend/evidence change;
4. repository Pages settings are confirmed to use **GitHub Actions** as the publishing source;
5. Olena explicitly approves the exact reviewed PR head for merge;
6. no unreviewed frontend bytes or submission claims are added.

Merge is a deployment-triggering action because the workflow runs on relevant pushes to `main`. Therefore a general code permission is not treated as sufficient approval for this merge.

## Expected public URL

```text
https://bit-nexus-labs.github.io/nexus-vector-keeperhub/
```

This is an expected location, not a verified submission link. Do not replace `PENDING_FRONTEND_URL` until:

1. the workflow reports a successful deployment;
2. the URL opens in a clean/incognito browser;
3. Simple, Technical, and Evidence views work;
4. the visible labels still state replay/sanitized/no-live-transaction;
5. no console errors, redirects, credential prompts, or horizontal overflow appear;
6. deployed frontend file digests match the reviewed repository artifact;
7. the link is rechecked from the submission draft.

## Rollback

If deployment validation or clean-browser review fails:

- do not insert the URL into the submission;
- do not alter runtime or transaction claims;
- correct through a new reviewed pull request;
- preserve the current public evidence manifest until the deployed bytes are verified.
