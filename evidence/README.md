# Public Evidence Bundle

This directory separates **offline-verified product claims** from **runtime evidence that has not yet been collected**.

`public_manifest.json` is public and sanitized. It lists reviewed merge commits, hashes the curated static replay artifacts, and explicitly records that no KeeperHub transaction hash, explorer URL, wallet operation, or funds movement is currently claimed.

Run the standard-library verifier from the repository root:

```powershell
py .\tools\verify_public_evidence.py
```

A future real testnet evidence update must be a separate reviewed change. It must use an exact public explorer URL and independently matched ERC-20 event, while keeping API keys, wallet material, raw provider payloads, internal identifiers and private receipts out of the public repository.
