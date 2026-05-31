# Project Heartbreaker

**Experimental Stark-tech frontend.** A safe fork of `packages/desktop`, reskinned toward
Iron Man 2 holographic UI — real futurism, not toy sci-fi.

## Safety contract

This is an isolated copy. It shares **nothing** with the stable app except the backend it
talks to. The stable client (`packages/desktop`) and the backend (`packages/api`) are never
touched by work in here. If Heartbreaker breaks, the real app is unaffected — just run
`desktop:dev` instead.

- App ID: `com.speda.heartbreaker` (distinct from `com.speda.desktop`)
- Dev ports: Electron renderer `5274`, web `5273` (stable uses default `5173`)
- Same backend: `http://localhost:8000`

## Running

From the repo root:

```bash
npm install                  # once — registers the new workspace
npm run heartbreaker:dev     # Electron app
npm run heartbreaker:web:dev # browser-only, on :5273
```

The stable app is unchanged:

```bash
npm run desktop:dev
```

Both can run at the same time (different ports / app IDs) for side-by-side comparison.

## Status

Fork created. Starting point is a 1:1 copy of the stable UI. The Stark redesign begins from
the Iron Man 2 concept references — holographic depth, volumetric panels, reactive HUD
elements, JARVIS blue. Nothing redesigned yet.
