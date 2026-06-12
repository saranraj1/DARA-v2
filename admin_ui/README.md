# DARA Admin UI

A React + Vite dashboard for monitoring the DARA autonomous bug resolution pipeline.

## Features

- **Live Pipeline Status** — see in-progress pipeline runs in real time
- **Fix History** — browse all generated fixes with confidence scores, diff previews, and outcomes
- **Strategy Analytics** — per-strategy win rates and feedback trends (RLHF dashboard)
- **Error Heatmap** — frequency and severity of errors by service and error class
- **Pattern Library** — view and manage learned fix templates

## Quick Start

```bash
# Install dependencies
cd admin_ui
npm install

# Start development server (proxies API to http://localhost:8000)
npm run dev
```

The UI will be available at **http://localhost:5173**.

> Requires the DARA API server to be running (`poetry run uvicorn main:app --reload`).

## Environment

The UI reads its API base URL from `admin_ui/.env`:

```env
VITE_API_BASE_URL=http://localhost:8000
```

For production, set `VITE_API_BASE_URL` to your deployed API endpoint.

## Build for Production

```bash
npm run build
# Output in admin_ui/dist/ — serve with any static file server
```

## Technology Stack

| Tool | Role |
|---|---|
| [Vite](https://vitejs.dev/) | Build tool + dev server |
| React 18 | UI framework |
| CSS Modules | Scoped styling |
| Fetch API | API client |

## API Authentication

The admin UI uses the DARA JWT token. Log in at `/login` with the credentials set in `.env`:

```env
ADMIN_USERNAME=dara
ADMIN_PASSWORD=your-admin-password
```

Tokens expire after `JWT_EXPIRE_HOURS` (default: 24 hours).
