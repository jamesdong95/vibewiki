# Contributing to VibeWiki

Thank you for helping shape VibeWiki. The project is currently an early-stage prototype, so the most useful contributions improve the evidence model, scanner design, fixture coverage, documentation, and verification discipline.

## Before opening a change

1. Read the product direction in [`README.md`](README.md) and the detailed plan in [`docs/product-development-plan.md`](docs/product-development-plan.md).
2. Keep local-first behavior and privacy as defaults.
3. Do not claim that a feature is implemented when it is only planned or mocked.
4. Never commit API keys, tokens, passwords, private keys, `.env` files, generated databases, or user repositories.
5. Run the offline check:

   ```bash
   python3 scripts/verify_preview.py
   ```

## Evidence-first changes

For analyzer or product-logic work, prefer deterministic facts with source references before adding language-model interpretation. A claim without evidence should be marked as an assumption or `unknown`.

## Pull requests

Please include:

- What changed and why.
- Which files or user-visible behavior changed.
- Verification commands and their real output.
- Any limitations, unsupported frameworks, or remaining unknowns.

Small, focused pull requests are easier to review and keep the evidence model coherent.
