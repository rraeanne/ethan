# Workspace

## Overview

pnpm workspace monorepo using TypeScript. Each package manages its own dependencies.

## Stack

- **Monorepo tool**: pnpm workspaces
- **Node.js version**: 24
- **Package manager**: pnpm
- **TypeScript version**: 5.9
- **API framework**: Express 5
- **Database**: PostgreSQL + Drizzle ORM (for Node.js services), SQLite (for Telegram bot)
- **Validation**: Zod (`zod/v4`), `drizzle-zod`
- **API codegen**: Orval (from OpenAPI spec)
- **Build**: esbuild (CJS bundle)

## Telegram Bot

A Python expense tracker bot for couples, located in `bot/`.

- **Runtime**: Python 3.12
- **Library**: python-telegram-bot 20.3
- **Database**: SQLite (`bot/expenses.db`) — persists between restarts
- **Entry point**: `bot/main.py`
- **Workflow**: "Telegram Bot" — runs `python bot/main.py`
- **Required secret**: `TELEGRAM_BOT_TOKEN`

### Bot Features
- Add personal or shared expenses
- Link with a partner via `/partner @username`
- View balance and who owes what
- Categories: Food, Transport, Entertainment, Utilities, Other
- Shared expenses split 50/50 automatically

## Key Commands

- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- `pnpm --filter @workspace/api-server run dev` — run API server locally

See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details.
