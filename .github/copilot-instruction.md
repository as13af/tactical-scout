<!-- Copilot Custom Instructions -->

## About Me

I'm a Football Data Scout/Analyst with a Computer Science background. I approach problems analytically — through data, patterns, and structure — the same way I read a match. Before scouting, I built custom Odoo modules (v14–v18) and worked with Django. I think in systems, enjoy finding elegant solutions, and care deeply about code quality.

My MBTI is ISFJ-T: detail-oriented, reliable, and conscientious. I prefer things to be well-structured and thoughtful rather than rushed or sloppy.

## Technical Background

- **Languages:** Python (primary), JavaScript, XML (Odoo/QWeb)
- **Frameworks:** Django, Odoo (v14–v18), familiar with React/Vue for frontend
- **Data tooling:** pandas, NumPy, SQL — comfortable with analytical pipelines
- **Domain expertise:** Football analytics, tactical data, scouting pipelines
- **Currently learning:** Always expanding CS knowledge — don't dumb things down

## Coding Preferences

- Use **Python 3.11+** with type hints on all function signatures
- Write **docstrings** on all public methods and classes (Google style preferred)
- Follow **PEP8**; format to 88-char line length (Black-compatible)
- Prefer **explicit over implicit** — I value readability over cleverness
- Use **meaningful variable names**; avoid abbreviations unless domain-standard (e.g., `xG`, `xA`)
- Always handle **edge cases and exceptions** explicitly — don't silently swallow errors
- Prefer **composition over inheritance** where reasonable
- For Django: follow standard MVT patterns; use class-based views unless a function-based view is clearly simpler
- For Odoo: respect ORM patterns, use `sudo()` sparingly and with a comment explaining why

## UI/UX Design Philosophy

### Aesthetic Standard — Dribbble-Quality UI

My web app UI should meet **Dribbble-level design standards**. This means:

- Study and reflect current design trends from Dribbble (2024–2025): clean layouts, generous whitespace, modern typography, purposeful micro-interactions
- Prefer **glassmorphism, soft shadows, and subtle gradients** over flat boxy UI — but keep it tasteful, not overdone
- Use **a refined color palette**: avoid default browser colors; always define a deliberate primary/accent/neutral system
- Typography matters: use **Inter, Geist, or DM Sans** as defaults; set proper line-height (1.5–1.7) and letter-spacing
- **Card-based layouts** with rounded corners (8–16px), subtle borders, and layered depth
- Every interactive element should have **hover and focus states** — no bare unstyled buttons
- Use **Tailwind CSS** where applicable; if not, write clean scoped CSS
- **Mobile-first and responsive** — assume the UI will be viewed on various screen sizes
- Prefer **smooth transitions** (150–300ms ease) on state changes
- Icons: use **Lucide, Heroicons, or Phosphor** — consistent set, no mixing styles

### What to Avoid

- Generic "Bootstrap default" aesthetic
- Harsh box shadows or thick borders
- Inconsistent spacing (always use a spacing scale: 4, 8, 12, 16, 24, 32, 48px)
- Walls of text — break content into digestible visual chunks
- Inaccessible color contrast — maintain WCAG AA minimum

## Communication Style

- Be **direct and concise** — I don't need hand-holding, but I appreciate clarity
- If there's a better approach than what I asked for, **say so and explain why**
- Don't over-comment obvious code — comment the **"why", not the "what"**
- When generating data-related code, think like an analyst: **correctness and performance matter**
- I work with football data frequently — understand terms like xG, progressive passes, pressing metrics, PPDA without explanation

## Project Context Hints

- I often build **data dashboards and scouting tools** — think tactical heatmaps, player comparison tables, match event timelines
- Odoo customizations follow the **module structure** (models, views, controllers, wizards, security)
- When in doubt about domain context, **ask one focused clarifying question** rather than assuming