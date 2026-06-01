# Superior Six — Agent UI Fork Guide

This package is the **base template** for all Superior Six agent interfaces.
It ships with a clean, neutral dark theme that reads well and stays out of the way.

Fork it once per agent. Change **exactly two files** to get a fully branded agent UI.

---

## The two files

### 1. `src/profile/config.ts` — identity & branding

```ts
// Everything visual and identity-related lives here.
// Change the name, colors, model number, suggested prompts — nothing else needs touching.

const PROFILE = {
  name: 'ULTRON',            // Agent name — shown in sidebar header + welcome screen
  modelNumber: 'MK I',       // Shown below the name in the sidebar
  avatarInitial: 'U',        // Initial shown in the footer avatar square
  userName: 'Ahmet',
  tagline: 'INTELLIGENCE CORE / MARK I',

  accent:      '#c84a3a',    // Primary accent color (hex) — drives the whole theme
  accentHover: '#e05a48',    // Hover state of accent

  apiBase: 'http://localhost:8000',
  apiKey:  'dev-key',

  suggestedPrompts: [
    'Run a threat assessment',
    'Analyse network topology',
    'Initiate protocol review',
    'Status report',
  ],
}

export default PROFILE
```

### 2. `src/theme/base.css` — visual design

Override the CSS variables at the top of `:root {}` to theme the whole UI in one block:

```css
:root {
  --accent:        #c84a3a;   /* match your profile accent */
  --accent-hover:  #e05a48;
  --accent-muted:  rgba(200,74,58,0.12);
  --bg-primary:    #0a0507;   /* page background */
  --bg-sidebar:    #110608;
  --text-primary:  #f0ddd9;   /* body text */
  /* ... rest stay as defaults */
}
```

That's it. The component tree, routing, chat logic, streaming, memory — all inherited.

---

## Workflow

```bash
# 1. Copy this package
cp -r packages/desktop packages/ultron

# 2. Edit the two files
#    packages/ultron/src/profile/config.ts
#    packages/ultron/src/theme/base.css

# 3. Run
cd packages/ultron && npm install && npm run dev
```

---

## Superior Six roster

| Agent | Codename | Domain | Accent color |
|---|---|---|---|
| Ultron | Analytical | Intelligence / research | Red `#c84a3a` |
| Sentinel | Finance | Markets / portfolio | Amber `#d39a3a` |
| Atomix | Science | Physics / engineering | Green `#4fa377` |
| Centurion | Security | Cyber / threat intel | Orange `#c8743a` |
| Nightcrawler | Operations | Recon / logistics | Purple `#9b72cf` |
| Optimus | Code | Dev / architecture | Cyan `#36abca` (same as SPEDA core) |

SPEDA Mark VI (this repo's `packages/heartbreaker/`) is the flagship — the others fork from here.
