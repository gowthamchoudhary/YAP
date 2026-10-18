# Frontend env documentation — this file names the env vars, it is a template for
# frontend/.env.local, which you create yourself (it is gitignored on purpose).
#
# Copy the lines you need into frontend/.env.local (Vite loads it automatically
# in dev; never commit the real file).
#
# ---------------------------------------------------------------------------
# Normal local dev: leave VITE_API_BASE unset (or empty). The Vite dev server
# already proxies /api and /ws to BACKEND_URL || http://localhost:8000
# (see vite.config.js), so http://localhost:5173 works with zero config.
#
# ---------------------------------------------------------------------------
# Set VITE_API_BASE to an ABSOLUTE base only when the frontend must call a
# backend that is NOT behind the dev proxy — e.g. running the backend on another
# machine, or a phone on the LAN hitting your PC directly:
#
#   VITE_API_BASE=http://localhost:8000
#
# The backend's CORS already allows http://localhost:5173 and
# http://127.0.0.1:5173 (FRONTEND_ORIGINS in backend .env to change).
#
# Legacy name still read for compatibility: VITE_API_URL
# ---------------------------------------------------------------------------
