# FrameWeaver dashboard

React, TypeScript, and Vite dashboard for the local FrameWeaver backend. It includes
project and scene management, narration, media editing, and a timeline editor.

## Development

Use Node.js 24 (the version used to verify this source snapshot).

```sh
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. Start the backend separately from the repository root
with `python -m agent.main`; Vite proxies `/api`, `/health`, and `/ws` to port 8100.
Google Flow generation also requires the extension and a signed-in Flow tab.

## Production build

```sh
npm run build
npm run preview
```

The output is `dist/`. For a separately hosted dashboard, copy `.env.example` to
`.env.production` before building and set the backend URLs for your installation.
The example points to the viewer's local machine; hosting the static dashboard
does not deploy the Python backend or share the maintainer's browser session.
Provider keys must stay in the backend environment, outside `VITE_*` variables.

## Checks

```sh
npm run lint
npm run build
npm run test:speech-repair
```

See [the sharing guide](../docs/SHARING.md) for backend setup and packaging.
