# Employee Attendance & Analytics API

FastAPI and MongoDB implementation for the HROne assignment.

## Run

1. Install dependencies: `pip install -r requirements.txt`
2. Set `MONGO_URI` and `MONGO_DB` in the environment.
3. Start MongoDB 6.0 or newer, then run `uvicorn app.main:app --port 8000` from the repository root.

The API creates its indexes at startup. `GET /health` checks MongoDB readiness. No secrets or `.env` file are included.

## Assignment files

The source contract, starter kit, and sample seed files are retained under `work/kit/candidate_kit` for local reference; only the required application and documentation files belong in a submission repository.
