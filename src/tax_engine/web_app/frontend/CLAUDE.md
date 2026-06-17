# Frontend rules

### Framework / tooling
- React 19 + Vite 6 + TypeScript (strict)
- Tailwind CSS v4 — all styling via utility classes, no inline `style={}`, no CSS modules
- Icons: `lucide-react` only — never emojis as UI elements
- Build: `npm run build` inside `frontend/` — outputs to `../dist/` (served by FastAPI)

### Code hygiene
- Max 200 lines per file (ESLint `max-lines` enforces this)
- One React component per file; file name matches the exported component
- No `any` types without an explanatory `// eslint-disable` comment
- All API types live in `src/types/api.ts`; reuse them — no ad-hoc inline types
- Utility functions (formatting, etc.) in `src/utils/`; API calls in `src/hooks/`

### Style conventions
- Dark theme throughout — zinc palette: `bg-zinc-950` body, `bg-zinc-900` surfaces, `bg-zinc-800` raised
- Text: `text-zinc-100` primary, `text-zinc-400` muted, `text-zinc-600` placeholder
- Gains: `text-green-400`, losses: `text-red-400`, warnings: `text-yellow-400`, accent: `text-blue-400`
- Numbers: `tabular-nums` class on all financial figures
- Icon size: `size={14}` inline with text, `size={16}` standalone

### Dev workflow
```bash
# Terminal 1 — Python backend
tax-web --port 8080

# Terminal 2 — Vite dev server (proxies /api to :8080)
npm run dev   # opens http://localhost:5173

# Build for production
npm run build  # outputs to ../dist/, served by FastAPI at /
```
