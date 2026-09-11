---
name: ui-ux
description: Use when building or changing screens, flows, or interactions in frontend/ — upload/status/chat UX, component structure, Next.js 16 App Router conventions, and how to verify a frontend change actually works in the browser.
---

# Conduit RAG — frontend UI/UX conventions

Applies to `frontend/`. This is about *behavior and structure* — flows, components, API wiring, verification. For visual styling/branding decisions, see [[design]].

## Next.js 16 is not the Next.js you remember

`frontend/AGENTS.md` says it directly: this version postdates training data and has breaking API/convention changes. **Before writing any App Router code** — especially route handlers — read the relevant guide under `frontend/node_modules/next/dist/docs/`. The one gotcha that bites most often: route handler `params` is `Promise<{...}>` and must be `await`ed, not read synchronously.

## Current app shape

- `app/page.tsx` + `components/AppShell.tsx` / `Sidebar.tsx` compose the shell; the two functional halves are **upload** (`FileUpload.tsx` → `FileList.tsx`, backed by `app/api/upload/*` and `app/api/files/*`) and **chat** (`ChatWindow.tsx`, backed by `app/api/query/route.ts`).
- `ChatWindow.tsx` is the reference pattern for a client component: `"use client"`, local `useState` for messages/input/sending, optimistic user-message append, `queryApi()` from `lib/api.ts`, error path renders inline as an assistant message rather than a toast/modal.
- Citations are a two-step pipeline: `lib/citations.ts`'s `extractCitationOrder` parses citation markers out of the answer text, `MessageText.tsx` renders the answer with those markers resolved, `SourcesAccordion.tsx`/`SourceCitation.tsx` render the backing chunks. Extend this chain rather than inventing a parallel citation mechanism if you touch retrieval display.
- `lib/` mirrors the Python `common/` layer on the Python side: `aws-clients.ts` (env-driven client construction), `bedrock.ts` (query embedding), `vectordb.ts` (pgvector search, same `DB_MODE` toggle as Python), `generation.ts` (Anthropic SDK, `ANTHROPIC_API_KEY` — deliberately not Bedrock, see `CLAUDE.md`'s LLM-agnostic split), `types.ts`, `api.ts`.

## The one asymmetry to never get wrong

`app/api/upload/route.ts`'s S3 client must use `AWS_ENDPOINT_URL_S3_PUBLIC` (browser-reachable) because the browser PUTs directly against the presigned URL it returns. Every other AWS client — DynamoDB, SQS, and the server-side S3 delete path — uses the docker-network-internal `AWS_ENDPOINT_URL_S3`/etc. Read the comments in `lib/aws-clients.ts` and `docker-compose.yml`'s `frontend` service before touching either endpoint variable.

## Procedure for a frontend change

1. If the change touches a route handler, App Router data-fetching, or anything Next-16-specific, check `node_modules/next/dist/docs/` first rather than relying on prior Next.js knowledge.
2. Reuse an existing component from `frontend/components/` before adding a new one; if a new one is genuinely needed, match the existing file's shape (named default export, `"use client"` only when it actually needs hooks/interactivity).
3. Wire new server-side AWS access through `lib/aws-clients.ts`'s existing pattern — don't construct a client ad hoc in a route handler.
4. **Actually run it**: `cd infra/local && docker compose up -d --build`, then either `python scripts/simulate_upload.py <file>` or drive `http://localhost:3000` directly — upload → status polling → chat query is the golden path. Test it in the browser before reporting the change done; type-checking and `npm run lint` verify correctness, not that the feature works.
5. `npm run lint` before calling it done.
