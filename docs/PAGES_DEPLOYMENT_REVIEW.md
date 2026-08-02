# GitHub Pages Deployment Review

## Status

Prepared in a draft pull request. No deployment occurs from the pull request branch.

## Source artifact

Only the reviewed `frontend/` directory is uploaded. The workflow does not build or rewrite UI bytes.

## Validation before upload

- tracked-repository hygiene verifier;
- public evidence verifier;
- focused static replay UI tests;
- Python 3.14 on `ubuntu-24.04`;
- exact-SHA-pinned GitHub-owned actions;
- checkout credentials are not persisted.

## Deployment boundary

The deploy job runs only when the workflow ref is `refs/heads/main` and the event is not `pull_request`.

Required deployment permissions are limited to:

```text
pages: write
id-token: write
```

The repository-level default remains `contents: read`.

## Manual gate

Before merge, GitHub Pages must be configured to use **GitHub Actions** as its publishing source in repository settings. Merge remains blocked until:

1. draft PR CI is green;
2. Vector exact workflow review passes;
3. Olena explicitly approves merge;
4. repository Pages settings are confirmed;
5. the expected public URL is reviewed.

## Expected URL

```text
https://bit-nexus-labs.github.io/nexus-vector-keeperhub/
```

Do not insert this URL into the submission draft until it opens in a clean browser and the deployed bytes match the reviewed repository artifact.
