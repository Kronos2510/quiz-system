# Quiz System

A private quiz-hosting platform for hosts and their participants. Hosts build multiple-choice and written quizzes, share an access code, and grade written answers when the timer is over. Multiple-choice questions score themselves; results stay sealed until the host releases them.

**Stack:** FastAPI · Jinja2 · SQLAlchemy · SQLite locally · Supabase Postgres in production · Vercel

---

## What it does

- **Admin-approved hosts.** New host accounts wait for a platform admin before they can sign in.
- **Host-approved participants.** Participants register, then a host (or the admin) approves the account.
- **Access-code-only entry.** There is no public quiz directory. A participant must redeem a code to enroll.
- **Two question formats.** Multiple-choice answers are auto-scored; written answers go to the host's grading queue.
- **Server-enforced timers.** Deadlines are stored in the database. Refreshing or closing the tab does not extend time, and an expired attempt auto-submits.
- **Autosave plus authoritative submit.** Answers save continuously, and the final submitted form is persisted before scoring.
- **Manual grading with context.** The host sees the prompt, model answer, participant response, points available, and feedback box side by side.
- **Release switch.** Participants cannot see scores or feedback until the host releases results.
- **Leaderboard and analytics.** Score distribution, pass rate, per-question accuracy, and a CSV export.
- **Dark neon UI.** Server-rendered pages with a small amount of vanilla JavaScript; no frontend build step.

---

## Quick start (local)

Requires Python 3.13 (3.11+ should also work).

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m uvicorn quizapp.main:app --reload
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -m uvicorn quizapp.main:app --reload
```

Open <http://127.0.0.1:8000>. On first start the app creates the SQLite schema (`dev.db`), bootstraps the admin account, and seeds demo content when `SEED_DEMO=1`.

### Demo accounts

| Role | Email | Password |
| --- | --- | --- |
| Admin | `admin@quizsystem.local` | `Admin1234!` |
| Host | `host@quizsystem.dev` | `Host1234!` |
| Participant | `maya@quizsystem.dev` | `Learner123!` |
| Participant | `leo@quizsystem.dev` | `Learner123!` |
| Participant | `sam@quizsystem.dev` | `Learner123!` |

Demo quizzes:

| Quiz | Access code | Format | State |
| --- | --- | --- | --- |
| World History Foundations | `QZ-HIST1` | 2 MCQ + 2 written | Published, results released |
| Python Basics Checkpoint | `QZ-PY231` | 4 MCQ | Published, results sealed |

Demo seeding is idempotent and only runs when the database has no quizzes. Delete `dev.db` to start over. The file is gitignored.

---

## The quiz lifecycle

1. **Register as a host.** The request goes to the admin approval queue.
2. **Admin approves the host** at `/admin/users`.
3. **Host creates a quiz** at `/host/quizzes/new`, adds questions, and publishes it. Publishing generates a code such as `QZ-7K2M9`.
4. **Participants register** and wait for a host to approve them. A host can approve participants from `/host/participants`; the admin can also do it from `/admin/users`.
5. **Participant enters the code** at `/join`, reviews the quiz intro, and starts a timed attempt.
6. **Answers autosave.** Multiple-choice questions are scored when the attempt is submitted.
7. **Host grades written answers** at `/host/attempts/{id}/grade` and finalizes the attempt.
8. **Host releases results** from the quiz results page. Participants then see scores, feedback, and the leaderboard.
9. **Host exports CSV** from `/host/quizzes/{id}/export.csv`.

Questions are structurally locked once the quiz has its first attempt, which protects stored answers from edits that would change their meaning.

---

## Environment variables

Copy `.env.example` to `.env` for local development. On Vercel, set the same names in **Project Settings → Environment Variables**.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_NAME` | `Quiz System` | Name shown in the UI and page titles. |
| `APP_TAGLINE` | `Host it. Take it. Grade it.` | Landing-page subtitle. |
| `DATABASE_URL` | `sqlite:///./dev.db` | Application database. Use a Supabase transaction-pooler URL in production. |
| `DIRECT_URL` | falls back to `DATABASE_URL` | Direct/session connection used only by `scripts/init_db.py`. |
| `SECRET_KEY` | dev fallback | Signs session cookies. **Set a long random value in production.** |
| `COOKIE_SECURE` | `0` | Set to `1` in production so cookies are HTTPS-only. |
| `SESSION_MAX_AGE` | `1209600` (14 days) | Session cookie lifetime in seconds. |
| `ADMIN_NAME` | `Platform Admin` | Display name for the bootstrap admin. |
| `ADMIN_EMAIL` | `admin@quizsystem.local` | Bootstrap admin email. |
| `ADMIN_PASSWORD` | `Admin1234!` | Bootstrap admin password. Change this before deploying. |
| `SEED_DEMO` | `1` | Seed demo users and quizzes on an empty database. Set to `0` in production. |

Generate a production secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

---

## Supabase setup

1. Create a project at [supabase.com](https://supabase.com).
2. Open **Project Settings → Database → Connection string** and copy both connection strings:
   - **Transaction pooler** (port `6543`) for `DATABASE_URL`.
   - **Session/direct** connection (port `5432`) for `DIRECT_URL` and for schema initialization.
3. Add the `postgresql+psycopg://` driver prefix and keep `?sslmode=require`:

   ```text
   postgresql+psycopg://postgres.PROJECT_REF:PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres?sslmode=require
   postgresql+psycopg://postgres.PROJECT_REF:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres?sslmode=require
   ```

4. Put the pooler URL in `DATABASE_URL` and the session/direct URL in `DIRECT_URL`.
5. Create the tables and bootstrap the admin once from your machine:

   ```bash
   python scripts/init_db.py
   ```

   This script uses `DIRECT_URL`, so DDL and seed queries do not travel through the transaction pooler.

The app uses `NullPool` and disables server-side prepared statements (`prepare_threshold=None`), which is required for PgBouncer-style transaction pooling on serverless platforms. The schema is also created automatically on application startup if it does not exist.

> This project uses its own session authentication and password hashing, not Supabase Auth. It connects to Postgres directly with SQLAlchemy; Supabase Row Level Security is not used. Authorization is enforced by the FastAPI routes and dependencies.

---

## Deploy to Vercel

Vercel provides zero-configuration FastAPI support. The root `app.py` exports `app`, and `requirements.txt` supplies the dependencies.

1. Push the project to GitHub, GitLab, or Bitbucket.
2. Import the repository at [vercel.com/new](https://vercel.com/new). Vercel should detect FastAPI automatically; no build command or output directory is needed.
3. Add production environment variables:

   ```text
   DATABASE_URL=postgresql+psycopg://...:6543/postgres?sslmode=require
   DIRECT_URL=postgresql+psycopg://...:5432/postgres?sslmode=require
   SECRET_KEY=<long random value>
   COOKIE_SECURE=1
   SEED_DEMO=0
   ADMIN_NAME=<your name>
   ADMIN_EMAIL=<your admin email>
   ADMIN_PASSWORD=<a strong password>
   ```

4. Run `scripts/init_db.py` once against Supabase with the production `DIRECT_URL` and `SEED_DEMO=0`, or let the first application start create the tables.
5. Deploy and verify `https://YOUR-PROJECT.vercel.app/health` returns `{"status":"ok", ...}`.
6. Sign in as the admin, approve your host account, and create your first quiz.

Static files in `public/` (CSS, JS, favicon) are served by Vercel's CDN. Locally the app mounts the same folders, so asset paths are identical in both environments.

### Deploying with the Vercel CLI

```bash
npm i -g vercel
vercel
vercel --prod
```

Set the environment variables in the Vercel dashboard before the production deploy.

---

## Testing

Install development dependencies and run the suite:

```bash
pip install -r requirements-dev.txt
pytest -q
```

The tests use a temporary SQLite database and cover registration/approval, role boundaries, the full quiz lifecycle (create → publish → join → autosave → submit → grade → release → CSV), access-code validation, and question locking.

---

## Project layout

```text
app.py                       Vercel entrypoint (re-exports the FastAPI app)
quizapp/
  main.py                    App setup, middleware, top-level routes
  config.py                  Environment-driven settings
  db.py                      Engine/session and SQLite/Supabase tuning
  models.py                  SQLAlchemy models and constants
  security.py                Password hashing and CSRF protection
  deps.py                    Auth/role dependencies
  seed.py                    Admin bootstrap and demo content
  routers/                   auth, admin, host, participant, JSON autosave API
  services/                  Attempt lifecycle, scoring, analytics, CSV export
  templates/                 Jinja2 pages
public/
  css/app.css                Neon design system
  js/app.js, js/quiz.js      UI interactions, timer, autosave
scripts/init_db.py           Direct-connection schema creation and seeding
tests/                       Pytest suite
```

---

## Known limitations

- **No email delivery.** Approvals are in-app; there is no verification or notification email.
- **No self-service password reset.** An admin can reset a user's password from `/admin/users`.
- **No migration framework.** Tables are created with `SQLAlchemy.create_all`; schema changes for an existing production database need a manual migration strategy (for example Alembic).
- **Manual written grading only.** There is no AI or keyword-based scoring for written answers.
- **One owner per quiz.** Quizzes belong to the host who created them; co-hosts are not supported.
- **No file uploads.** Written answers are plain text.
- **Session-cookie auth.** Sessions are signed cookies, not server-side session records; changing `SECRET_KEY` signs everyone out.
