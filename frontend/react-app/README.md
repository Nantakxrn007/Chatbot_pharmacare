# PharmaCare AI — React frontend

React + TypeScript + Vite port of the pharmacist chat app frontend. Backend (FastAPI) is unchanged apart from routing (see below).

## Status
This is now the **only** frontend — the old hand-written HTML/CSS/JS pages have been removed. All pages are ported:
- Login (`/login`)
- Chat (`/`) — sessions sidebar, streaming chat, edit/regenerate, PDF citation panel, token summary modals
- Patients list (`/patients`)
- Patient detail (`/patient/:name`) — AI summary, PDF/Excel export, session list
- Test Cases (`/testcase`) — run/stop, filters, detail modal, CSV export, accuracy chart

## Run it (dev)
```
cd frontend/react-app
npm install
npm run dev
```
Opens on `http://localhost:5173`, proxying `/api` and `/data` to the FastAPI backend at `http://localhost:8000` (override with `VITE_API_TARGET`, e.g. `http://localhost:8899` for the Docker setup).

## Build & serve via the backend
```
cd frontend/react-app
npm install
npm run build
```
This produces `frontend/react-app/dist/`. `backend/main.py` serves this app at every page route (`/`, `/login`, `/patients`, `/patient/{name}`, `/testcase`) — it checks for `dist/index.html` at startup, no restart-time config needed.

Until you run `npm run build`, every page route returns a 503 with a message telling you to build it — there is no fallback page anymore.
