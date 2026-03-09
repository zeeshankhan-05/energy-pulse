# Deployment Notes

## Local Build Test

Run this from the project root to validate all images build successfully before pushing:

```bash
docker compose -f docker-compose.prod.yml build
```

---

## Critical Issues to Resolve Before Deploying

### 1. Gunicorn entry point — CONFIRMED: `main:app`
The FastAPI `app` instance is defined in `backend/main.py`, so `main:app` is correct.
If you ever move the app object into `backend/app/main.py`, change the Dockerfile.prod
CMD to `app.main:app`.

### 2. `gunicorn` is not in requirements.txt
`backend/Dockerfile.prod` installs gunicorn directly in the builder stage as a workaround,
but you should add it to `requirements.txt` to keep the dependency explicit:

```
gunicorn==22.0.0
```

### 3. ~~`app.tasks.celery_app` does not exist~~ — RESOLVED
`backend/app/tasks/celery_app.py` has been created. `refresh_all_summaries` in
`summary_tasks.py` is now decorated with `@celery_app.task`.

### 4. Node version — `node:18-alpine` is insufficient
This project uses Vite 7 + Tailwind v4 (`@tailwindcss/vite`), which require Node >= 20.19.
`frontend/Dockerfile.prod` already uses `node:22-alpine`. Do **not** downgrade.

---

## Environment Variables Required

All of the following must be exported in the shell (or written to a `.env` file loaded by
docker compose) before running `docker compose -f docker-compose.prod.yml up`:

| Variable            | Description                                      |
|---------------------|--------------------------------------------------|
| `DATABASE_URL`      | PostgreSQL connection string                     |
| `REDIS_URL`         | Redis connection string                          |
| `POSTGRES_PASSWORD` | Password for the `postgres` service              |
| `ANTHROPIC_API_KEY` | Anthropic API key for AI summary generation      |
| `EIA_API_KEY`       | U.S. Energy Information Administration API key  |
| `SENDGRID_API_KEY`  | SendGrid API key for email alerts                |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook URL for alert delivery    |

### For ECR push (`scripts/push_to_ecr.sh`) and EC2 setup (`scripts/setup_ec2.sh`):

| Variable            | Description                                                   |
|---------------------|---------------------------------------------------------------|
| `AWS_ACCOUNT_ID`    | AWS account ID (12-digit number)                              |
| `AWS_REGION`        | AWS region — hardcoded as `us-east-2` in scripts              |
| `ECR_BACKEND_URI`   | Full ECR URI for backend, e.g. `123456789.dkr.ecr.us-east-2.amazonaws.com/energypulse-backend:latest` |
| `ECR_FRONTEND_URI`  | Full ECR URI for frontend, e.g. `123456789.dkr.ecr.us-east-2.amazonaws.com/energypulse-frontend:latest` |
| `POSTGRES_USER`     | PostgreSQL username (e.g. `energypulse`)                      |
| `POSTGRES_DB`       | PostgreSQL database name (e.g. `energypulse`)                 |
| `SECRET_KEY`        | Application secret key for signing tokens                     |

### nginx / SSL note
`setup_ec2.sh` installs host nginx and certbot, but the frontend container also binds
port 80. To use HTTPS with certbot, change the `frontend` ports in `docker-compose.prod.yml`
to `"8080:80"` and configure host nginx to reverse-proxy `localhost:8080`. Then run:
```bash
certbot --nginx -d your-domain.com
```

---

## Deployment Checklist

- [ ] Add `gunicorn==22.0.0` to `backend/requirements.txt`
- [x] Create `backend/app/tasks/celery_app.py` with a `Celery` instance
- [ ] Set all required environment variables
- [x] Run `docker compose -f docker-compose.prod.yml build` and confirm no errors
- [ ] Run `bash scripts/push_to_ecr.sh` to push to ECR
- [ ] SSH to EC2, copy `docker-compose.prod.yml` to `/tmp/`, export env vars, run `sudo bash scripts/setup_ec2.sh`
- [ ] For subsequent deploys: `bash ~/energypulse/scripts/deploy.sh`
