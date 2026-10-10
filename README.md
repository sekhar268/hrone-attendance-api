# Employee Attendance & Analytics API

FastAPI and MongoDB implementation for the HROne assignment, with an optional protected demo mode.

## Run locally

1. Install dependencies: `pip install -r requirements.txt`
2. Set `MONGO_URI` and `MONGO_DB` in the environment (defaults: local MongoDB and `attendance_db`).
3. Start MongoDB 6.0 or newer, then run `uvicorn app.main:app --port 8000` from the repository root.
4. Open `http://localhost:8000/docs` to try the API.

The API creates its indexes at startup. `GET /health` checks MongoDB readiness. No secrets or `.env` file are included.

## Protected online demo

`render.yaml` describes a small Render web service. It uses the free instance plan and prompts for secret environment values instead of storing them in Git. Free services can sleep when idle and have usage limits; see [Render's free-instance notes](https://render.com/docs/free).

1. Create a **separate MongoDB Atlas demo cluster** and database user. Use a dedicated database named `attendance_demo`; do not point this demo at real employee data. Atlas requires a database user and an IP access list entry for connections ([Atlas connection guide](https://www.mongodb.com/docs/atlas/connect-to-database-deployment/)).
2. In Render, create a Blueprint from this public repository and provide `MONGO_URI`, `DEMO_USERNAME`, and `DEMO_PASSWORD` when prompted. Choose a strong, unique demo password and keep it private.
3. In the Render service dashboard, copy its outbound IP ranges from **Connect → Outbound** and add those ranges to Atlas **Network Access**. Avoid opening Atlas to all IP addresses.
4. The service seeds three synthetic example employees and their October 2026 records on first startup. A marker prevents later restarts from replacing demo edits.
5. Open the service's `/docs` URL. The browser prompts for the demo username and password; all API routes, including docs and health, require them in production mode.

The demo seeder refuses to run against any database other than `attendance_demo`, and only inserts its clearly synthetic demo records. It never deletes other documents. Do not use this demo for real employee records or production attendance.
