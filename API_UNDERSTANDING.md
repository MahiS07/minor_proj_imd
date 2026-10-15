# METAR GUI v2 - API Understanding Document

## 1) API Architecture at a Glance

The backend is a Flask app with 3 main route groups:

- `Auth APIs` under `/auth/*` in [app/backend/auth.py](app/backend/auth.py)
- `Core processing APIs` under `/api/*` in [app/backend/routes/api.py](app/backend/routes/api.py)
- `Web/helper routes` (no prefix) in [app/backend/routes/web.py](app/backend/routes/web.py)

Blueprint registration is done in [app/backend/__init__.py](app/backend/__init__.py#L29).

Base URLs by runtime:

- Local Flask run: `http://localhost:5000`
- Docker + Nginx:
  - Frontend entry: `http://localhost:8080`
  - Backend API direct (optional): `http://localhost:5000`

---

## 2) Authentication Model

- Login sets JWT in `auth_token` cookie (8-hour expiry).
- Auth works via cookie first, then `Authorization: Bearer <token>` fallback.
- Role hierarchy:
  - `user` < `admin` < `super_admin`
- Role guard decorator: `@require_role(...)`

Reference: [app/backend/auth.py](app/backend/auth.py)

---

## 3) Auth API Endpoints (`/auth/*`)

### Public

1. `POST /auth/signup`
- Purpose: Register a user account.
- Input: JSON with `username`, `password`.
- Notes: Username is converted to uppercase; role forced to `user`.

2. `POST /auth/login`
- Purpose: Authenticate and issue JWT cookie.
- Input: JSON/form with `username`, `password`.
- Output: JSON (`message`, `role`, `token`) + `auth_token` cookie.

3. `POST /auth/logout`
- Purpose: Clear auth cookie.

4. `GET /auth/me`
- Purpose: Return current authenticated user.

### User/Admin/Super Admin protected

5. `POST /auth/change-password` (user+)
- Input: `current_password`, `new_password`

6. `GET /auth/users` (admin+)
- List normal users.

7. `PUT /auth/users/<user_id>` (admin+)
- Update user password/active status.

8. `DELETE /auth/users/<user_id>` (admin+)
- Delete normal user.

9. `POST /auth/create-user` (admin+)
- Create user with generated default password `<STATION>@123`.

10. `GET /auth/admins` (super_admin)
- List admins.

11. `PUT /auth/admins/<admin_id>` (super_admin)
- Update admin active status.

12. `DELETE /auth/admins/<user_id>` (super_admin)
- Disable admin.

13. `POST /auth/create-admin` (super_admin)
- Create admin with generated default password `<STATION>@123`.

14. `GET /auth/all` (super_admin)
- Combined list of users + admins.

15. `DELETE /auth/remove/<user_id>` (super_admin)
- Delete any non-super-admin account.

---

## 4) Core API Endpoints (`/api/*`)

### METAR verification flow

1. `GET /api/get_metar`
- Query: `start_date`, `end_date` (`YYYYMMDDHHMM`), `icao`
- Purpose: Fetch METAR from OGIMET and return text file download.

2. `POST /api/process_metar`
- Content-Type: `multipart/form-data`
- Inputs:
  - `forecast_file` (required)
  - `observation_file` (optional if start/end provided)
  - `start_date`, `end_date` (optional if observation file provided)
  - `icao` (required)
- Output: JSON with metrics + encoded download tokens in `file_paths`.

3. `GET /api/download/<file_type>`
- Query: `file_path` (encoded token from process response)
- Supported file types: `metar`, `metar_csv`, `comparison_csv`, `merged_csv`

4. `GET /api/accuracy_chart`
- Query: `metric` (default `Overall`)
- Output: HTML chart (Plotly).

### Upper-air flow

5. `GET /api/get_upper_air`
- Query: `datetime`, `station_id`
- Output: downloaded CSV from upper-air source.

6. `POST /api/process_upper_air`
- Content-Type: `multipart/form-data`
- Inputs: `station_id`, optional `datetime`, optional files (`observation_file`, `forecast_file`)
- Output: JSON with accuracies and generated Excel file path.

7. `GET /api/download/upper_air_csv`
- Query: `file_path`
- Output: file download (`.csv`/`.xlsx`).

### Aerodrome warning flow

8. `POST /api/upload_ad_warning`
- Upload `file` (`.txt` warning).
- Saves `AD_warning.txt`, prepares/copies METAR context.

9. `POST /api/adwrn_verify`
- Runs warning-vs-METAR validation and report generation.
- Output includes detailed accuracy and station validation info.

10. `GET /api/download/adwrn_report`
- Downloads generated warning report CSV.

11. `GET /api/download/adwrn_table`
- Downloads generated warning table Excel (`Aerodrome_Warnings_Table.xlsx`).

### Activity/log APIs

12. `GET /api/logs/user/<user_id>` (admin+)
- Paginated logs for one user.

13. `GET /api/logs/all` (admin+)
- Paginated logs for all users (role-filtered for admin).

14. `GET /api/logs/stats` (admin+)
- Aggregated usage stats.

15. `POST /api/logs/clear` (super_admin)
- Delete all logs.

16. `POST /api/logs/log-verification` (user+)
- Record verification activity.

17. `GET /api/logs/users-list` (super_admin)
- User list for filtering in logs UI.

---

## 5) Web/Helper Routes Used by Frontend

These are defined in [app/backend/routes/web.py](app/backend/routes/web.py):

- `GET /login`, `GET /signup`, `GET /admin`, `GET /superadmin`, `GET/POST /`
- `GET/POST /fetch_metar`
- `GET /bar_chart`

Also available:

- `GET /health` in [app/backend/__init__.py](app/backend/__init__.py#L40)

---

## 6) What Changes Between Local vs Docker Run

## A) Entry point and request routing

Local (`python app/backend/app.py`):
- Flask serves frontend templates and backend routes from one process on port 5000.
- Unprefixed web routes like `/fetch_metar` and `/bar_chart` are directly reachable.

Docker (`docker-compose up -d --build`):
- Nginx serves frontend on port 8080.
- Nginx proxies only these backend paths:
  - `/api/*`
  - `/auth/*`
  - `/web/*`
  - `/health`
- Important impact: unprefixed backend routes (`/fetch_metar`, `/bar_chart`) are not proxied by current Nginx config, so frontend calls to these paths may fail or return `index.html`.

References:
- [nginx/default.conf](nginx/default.conf)
- [app/frontend/js/app.js](app/frontend/js/app.js#L1542)
- [app/frontend/js/app.js](app/frontend/js/app.js#L242)

## B) Data directory base path

Path decision in [app/backend/config.py](app/backend/config.py):

- Local: data root resolves to project path `app/data`
- Docker: data root is `/app/data` inside container, mounted from host `./app/data`

Effect:
- Same logical folders (`metar_data`, `upper_air_data`, `ad_warn_data`) exist in both runtimes.
- Docker persists data across container restarts through volume mounts.

## C) Database location behavior

- SQLAlchemy URI uses `DOCKER_VOLUME_MOUNT_POINT/auth_test.db`.
- Local: DB at project `app/data/auth_test.db`.
- Docker: DB at `/app/data/auth_test.db` in container, persisted to host `./app/data/auth_test.db`.

Reference: [app/backend/config.py](app/backend/config.py#L22)

## D) Web server/runtime behavior

Local:
- Flask development server with `debug=True` in [app/backend/app.py](app/backend/app.py#L17).

Docker:
- Gunicorn runs Flask app (`app.backend:create_app()`) from [Dockerfile](Dockerfile#L36).
- Nginx adds proxy/cache/compression/security headers from [nginx/default.conf](nginx/default.conf).

## E) Cookie/HTTPS behavior

In login API ([app/backend/auth.py](app/backend/auth.py#L194)):
- Cookie `secure`/`SameSite` depend on `USE_HTTPS` env and `X-Forwarded-Proto`.
- Behind Nginx, forwarded headers affect this decision.

## F) Timeouts and ops config

- Docker compose provides OGIMET-related env vars (`OGIMET_TIMEOUT`, retries/cache flags).
- Nginx/Gunicorn timeouts are configured for longer METAR operations.

Reference: [docker-compose.yml](docker-compose.yml), [Dockerfile](Dockerfile), [nginx/default.conf](nginx/default.conf)

---

## 7) Practical API Call Order (Most Common Flows)

### METAR verification

1. `GET /api/get_metar` (optional if observation file already available)
2. `POST /api/process_metar` (forecast + observation/date range)
3. `GET /api/download/comparison_csv?file_path=...`
4. `GET /api/download/merged_csv?file_path=...`
5. `GET /api/accuracy_chart?metric=Overall`

### Upper-air verification

1. `GET /api/get_upper_air` (optional)
2. `POST /api/process_upper_air`
3. `GET /api/download/upper_air_csv?file_path=...`

### Aerodrome warning verification

1. `POST /fetch_metar` (web route, not `/api`)
2. `POST /api/upload_ad_warning`
3. `POST /api/adwrn_verify`
4. `GET /api/download/adwrn_report`
5. `GET /api/download/adwrn_table`

---

## 8) Known Route Mismatches to Watch

1. Frontend uses `/fetch_metar` and `/bar_chart`, but Docker Nginx currently does not proxy those unprefixed backend routes.
2. Frontend references `/chart_template` in [app/frontend/js/app.js](app/frontend/js/app.js#L234), but backend route was not found.

If you want, I can generate a second document with exact request/response examples (`curl` + sample JSON) for each API and also patch Nginx so local and Docker routing behave the same.
