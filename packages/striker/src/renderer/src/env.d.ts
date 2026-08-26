/**
 * Ambient types for the renderer.
 *
 * `tsconfig.web.json` includes only `src/renderer/src/**`, so neither of these
 * reached the type checker on its own:
 *
 *  · `vite/client` — which is what declares `import.meta.env`, read at startup
 *    to pick the brand (VITE_AGENT) and the API base.
 *  · the preload's `declare global` — which is what declares `window.api`, the
 *    contextBridge surface (get-config, window controls, open-external,
 *    select-directory).
 *
 * Without them every `window.api.…` and `import.meta.env.…` in the renderer was
 * a type error, and because the ROOT tsconfig lists no files of its own — only
 * project references — `tsc --noEmit` checked nothing and never said so.
 */

/// <reference types="vite/client" />
/// <reference path="../../preload/index.d.ts" />
