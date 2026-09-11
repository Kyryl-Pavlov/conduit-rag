---
name: design
description: Use when making visual/styling decisions in frontend/ — color tokens, dark mode, spacing, typography, iconography — to keep changes consistent with this project's existing (minimal, hand-rolled Tailwind) visual identity, as opposed to introducing a new design system.
---

# Conduit RAG — visual design conventions

Applies to the *look* of `frontend/`: color, type, spacing, dark mode, iconography. For flows/components/behavior, see [[ui-ux]]. This is a project-specific styling reference, distinct from the built-in `design` canvas skill (which drafts a standalone mockup artifact) — use that one for exploratory mockups outside the repo, use this one for implementing changes to the actual app.

## Current visual identity (as it stands today)

This is a minimal, utilitarian UI, not a themed/branded one — no illustration, no marketing polish, function-first. Match that register unless the user explicitly asks for a visual rebrand.

- **Color**: Tailwind v4 tokens defined CSS-first in `frontend/app/globals.css` — `--background`/`--foreground` in `:root`, overridden under `[data-theme="dark"]`, exposed to Tailwind via `@theme inline`. Component-level color is otherwise just Tailwind's default `zinc` neutral scale (`zinc-100`/`zinc-200`/`zinc-800`/`zinc-900`) plus a single accent, `blue-600`, used for the primary action (send button, user message bubble). There is no larger custom palette — don't introduce new named colors without a reason; reach for `zinc`/`blue` first.
- **Dark mode**: driven by a `[data-theme="dark"]` attribute (via `@custom-variant dark`) toggled by `ThemeToggle.tsx`, **not** a bare `prefers-color-scheme` media query. Any new color-bearing class needs an explicit `dark:` variant alongside it (see `ChatWindow.tsx` for the pattern — `border-zinc-200 dark:border-zinc-800`, etc.), matching the pair, not just adding dark styling in isolation.
- **Typography**: `body` is set to `Arial, Helvetica, sans-serif` directly. Note the `--font-sans`/`--font-geist-sans` theme tokens exist (from the Next.js/Geist scaffold) but currently aren't what `body` actually uses — check `globals.css` before assuming Geist is live; don't silently "fix" this without flagging it, since it may be intentional or simply unfinished.
- **Spacing/shape**: Tailwind utility classes directly in components, no design-token abstraction layer beyond the CSS variables above. `rounded-lg` / `rounded-md` for containers and inputs, `gap-2`/`gap-3`/`gap-4` for flex spacing, `text-sm` as the dominant body size. No component library (no shadcn/ui, MUI, etc.) — everything is hand-rolled Tailwind classes in `.tsx` files.
- **Icons**: hand-rolled SVGs in `components/icons.tsx`, not an icon library import. Follow that pattern for new icons rather than pulling in `lucide-react`/`heroicons`/etc.

## Procedure for a styling change

1. Decide whether this is genuinely a new design decision or should reuse an existing token/class — prefer extending `globals.css`'s `:root`/`[data-theme=dark]` pair over hardcoding a new color in a component.
2. If adding a new color token, define both the light and dark value together in `globals.css`, mirroring the existing `--background`/`--foreground` pattern, rather than a Tailwind color class hardcoded in one place.
3. Keep the minimal, information-first register — no decorative gradients, shadows, or motion beyond what the app already has, unless the user is explicitly asking for a redesign rather than a fix.
4. If the ask is actually "design me a new look/mockup" rather than "implement this in the app," that's the built-in `design` canvas skill's job (draft artboards, get user sign-off), not this one — this skill is for landing a styling change directly in the codebase.
5. Verify in the browser in both light and dark mode (`ThemeToggle`) before calling a styling change done — see [[ui-ux]]'s verification procedure.
