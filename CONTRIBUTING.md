# Contributing

Describe the problem, steps to reproduce, and expected behavior when opening an
issue. Include Python/Node/Chrome versions and relevant errors with credentials,
cookies, signed media URLs, and personal project data removed.

For changes, keep pull requests focused and explain what changed and how it was
verified. Preserve copyright notices and document the origin and license of any
new third-party code, model, or media asset.

## Local checks

From the repository root, with the backend virtual environment active:

```sh
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/unit
node --test tests/*.test.cjs tests/*.test.mjs dashboard/tests/speechRepair.test.ts
```

From `dashboard/`:

```sh
npm ci
npm run build
```

Python 3.12 and Node.js 24 were used for the source-sharing review. Live Google
Flow generation requires your own signed-in browser and consumes your own service
credits. Use the batch API for generation and follow `AGENTS.md` and `CLAUDE.md`.

## Source packages

```sh
python tools/package_source.py --output releases/frameweaver-source.zip
```

The packager excludes local data and checks for common credential patterns.
Inspect the generated manifest and archive before publication. Automated pattern
checks do not establish that a release is free of every possible secret.
