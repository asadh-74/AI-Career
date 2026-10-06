# Career Atlas — Flutter and FastAPI

Live at https://career-atlas-yhx1.onrender.com/

## What works

- Flutter UI for web and Android: login, company boards, live listings, PDF CV/Resume upload, AI application preparation, official application links, and a review queue.
- FastAPI API with PostgreSQL persistence on Render (SQLite for local development), one-user password login, PDF text extraction, Greenhouse and Lever public company job feeds, deduplication, Gemini match rationale and a cover note. If Gemini fails, a clearly labeled local keyword estimate and generic cover note remain available.
- **Find jobs** scans configured company boards on demand. This free Render configuration does not include scheduled scanning or instant updates.
- The application queue never reports **Applied** without an actual employer confirmation recorded by the user.
- Every imported company job has a Submitted / Not submitted toggle. You can record applications you already sent on the employer's website without an AI draft. The statuses are stored in a new database table.
- Setup includes quick-add links for Canonical, Smart Working Solutions, Educative, and Xapo Bank. These are public company boards; each posting's location and eligibility still need review. You can add other Greenhouse and Lever boards by URL.
- The Jobs screen opens Indeed Pakistan, Glassdoor Pakistan, ROZEE.PK, and Mustakbil for direct browsing. Indeed uses the typed search query. These external websites do not feed the in-app job count, AI drafts, or scheduled scan; integrated import requires an authorized feed or partner access. Applications on them are completed by the user on the external site.

## Application submission status

The API's `/api/applications/{id}/submit` endpoint returns `needs_human` and the official form URL. Greenhouse and Lever publishing APIs permit public listing access but their POST application endpoints require employer-issued API keys. They also have custom required questions. This code does not claim to auto-submit across company sites. The AI drafts are grounded in your uploaded PDF text, but must be reviewed before use. Browser form adapters can be built for specific sites after real form testing, with CAPTCHA and custom questions sent for human completion.

## Local backend

Install Python 3.12+, open PowerShell in `backend`, then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-windows.ps1
```

On the first run, it installs dependencies and asks for your password. Later runs skip installation. `setup_local.py` creates a private `.env`; edit it to add a **new** `GEMINI_API_KEY`. SQLite is used locally. The API is at `http://127.0.0.1:8000`.

To use **Gemini AI matching**, upload a text-selectable PDF under **Setup → Resume** or **CV**, then put a valid Gemini API key in `backend/.env` as `GEMINI_API_KEY=...` and restart the backend. Without a document, the Jobs screen offers an upload button. If Gemini rejects a request, the result states the HTTP status and uses a local keyword estimate, explicitly marked as non-AI. After fixing the key or quota, tap **Match & draft** on that job again to retry Gemini. Job status tracking needs neither a document nor an API key.

When updating an existing local install, replace **both** `backend/main.py` and `flutter/lib/main.dart` from this archive. Keep your existing `backend/.env` and `backend/career.db`. Restart the backend and Flutter. The new status table is created automatically without deleting existing jobs.

## Flutter on Windows

Install the Flutter SDK using the [official VS Code instructions](https://docs.flutter.dev/install/quick). Run `flutter doctor`. From `flutter`, run:

```powershell
flutter create . --platforms android,web --project-name career_atlas
flutter pub get
flutter run -d chrome --web-port 3000 --dart-define=API_BASE_URL=http://127.0.0.1:8000
```

For Android on a physical phone, use the deployed Render URL in `API_BASE_URL`. A local backend at `127.0.0.1` is the phone itself, not your PC. Build the Android APK with `flutter build apk --release --dart-define=API_BASE_URL=https://YOUR-SERVICE.onrender.com`.

## Render deployment

`render.yaml` defines a Free web service serving Flutter web and FastAPI at the same URL, plus a Free Render Postgres database. Review the Render estimate before applying. The Free database expires after **30 days**, with no included backups. You must upgrade or export your data before expiration. The Free web service sleeps after inactivity; its filesystem is ephemeral. There is no cron job in this configuration, so use **Find jobs** to scan on demand.

1. Use the existing repository `https://github.com/asadh-74/AI-Career`. It currently contains the project with `render.yaml` at the root and is public. `.env`, `.venv`, and `career.db` have not been uploaded.
2. In Render, create a Blueprint from that repository and review that both the web service and database show **Free** before applying.
3. Enter `ADMIN_PASSWORD_HASH` from `make_password.py` and a fresh `GEMINI_API_KEY` when prompted. Rotate the previously shown Gemini key before this step. Render generates `SESSION_SECRET`. Never upload the local `.env` or put secrets in Flutter `--dart-define`.
4. After deployment, open the web service URL, log in, upload your CV and Resume, paste real Greenhouse or Lever company careers URLs, then tap **Find jobs**.
5. Use the same URL as `API_BASE_URL` when building Android.

The hosted PostgreSQL database starts empty. Local jobs, uploaded PDFs, and submitted statuses in `career.db` are **not** transferred automatically. Add your boards and upload your documents again after deployment, or plan a private migration separately. Uploaded PDFs are stored in PostgreSQL; review your Render plan and backups before relying on it as your only copy.

In Render choose **New → Blueprint**, connect that repository, confirm that the web service and Postgres database both show Free, then apply. The Docker build includes Flutter web and FastAPI at one HTTPS URL, so the web app needs no localhost API setting. Tap **Find jobs** whenever you want fresh listings. The Android app is a separate APK build; Render hosts the web app and API, not an Android app store release.

### Limits

- Adding a company means pasting a Greenhouse or Lever hosted HTTPS careers URL. The app derives the board slug. Arbitrary company sites, Workday, LinkedIn, and Indeed are not universal sources.
- No Flutter SDK or Render account was available in the build workspace, so the Dart app and cloud deployment must be compiled and validated on your Windows machine and Render account. The backend has automated API tests.
- Password, uploaded PDF bytes, and extracted text are private in the database, but the Free database has no backups and expires after 30 days. Keep your own copies of uploaded PDFs, and do not reuse the exposed Gemini key.


## Career Atlas Automation v2

This branch adds a fail-safe job application worker on top of the existing FastAPI/Flutter application.

### Added

- Playwright as the primary browser form automation engine.
- Selenium as a fallback browser adapter.
- Email application routing when a job description contains a valid application email.
- Gemini-backed match scoring and drafting reuse from the existing application pipeline.
- Configurable match threshold, daily application limit, remote-only filtering, and provider switches.
- Automatic resume/CV attachment for email and browser applications.
- Safety stops for CAPTCHA, employer assessments, work authorization/sponsorship, salary, clearance, demographic, criminal-history, and other uncertain questions.
- Application receipts/status tracking back into the existing PostgreSQL/SQLite tables.
- `GET /api/automation/config` and `POST /api/automation/run` endpoints.
- A scheduled GitHub Actions worker every six hours plus manual workflow dispatch.

### Important behavior

`AUTO_SUBMIT_BROWSER` defaults to `false`. With that default, Playwright fills a recognized form and stops before the final submission button. Set it to `true` only after testing the target application flows. Even then, the worker refuses to guess sensitive or uncertain screening answers and never bypasses CAPTCHA or anti-bot checks.

The worker does not use an AI-detector evasion service. It performs a small writing-quality pass and keeps Gemini grounded in the uploaded resume/CV and job description.

### Required automation configuration

Copy the new variables from `backend/.env.example`. For GitHub Actions, configure repository secrets for the hosted `DATABASE_URL`, Gemini key, verified `APPLICANT_PROFILE_JSON`, and optional SMTP credentials. Configure repository variables such as `AUTO_APPLY`, `MIN_MATCH_SCORE`, `MAX_APPLICATIONS_PER_DAY`, and `AUTO_SUBMIT_BROWSER`.

For Gmail SMTP, use an account-specific credential rather than your normal Google password. Do not commit credentials to the repository.


## Career Atlas Automation v3

Career Atlas v3 keeps the existing jobs/applications tables and adds an additive intelligence layer around them.

### v3 capabilities

- **ATS adapters:** deterministic field targeting for Greenhouse, Lever, Ashby, SmartRecruiters, Workday and generic employer forms.
- **Form memory:** unknown non-sensitive fields are recorded by host and can be mapped to verified profile keys for later applications. Sensitive fields are never written into normal field memory.
- **Tailored resume variants:** generates a per-job PDF using only text already present in the uploaded resume; it reorders verified material and never invents achievements.
- **Multidimensional matching:** stores technical, experience, location, seniority, education, salary, form difficulty and application-probability scores.
- **Application probability:** combines fit and form difficulty so easy, strong applications can be attempted before difficult low-value forms.
- **CrewAI research layer:** three-agent source/eligibility/ranking research is available when `CREWAI_LLM` is configured. A deterministic research fallback is used otherwise so normal runs stay fast and cheap.
- **Official source expansion:** company sources can use Greenhouse, Lever, Ashby and SmartRecruiters feeds. Public remote boards remain supported.
- **Duplicate protection:** source-level duplicate checks plus normalized cross-board fingerprints prevent repeated submissions to equivalent roles.
- **Reusable profile answers:** verified non-sensitive answers are reused through field memory. Country-specific legal wording still stops for review rather than being inferred.
- **Smart retries:** failures are classified (timeout, selector, CAPTCHA, assessment, confirmation, sensitive field, unknown field, etc.) and recoverable failures receive one bounded retry.
- **Submission evidence:** Playwright stores before/after submission screenshots in PostgreSQL when a submission attempt reaches the final button.
- **Pipeline tracking:** application events use discovered → matched → prepared → applying → submitted → employer viewed → interview/rejected/offer.
- **Recruiter inbox monitor:** optional IMAP monitoring classifies recruiter replies without marking messages read and updates the application pipeline.
- **Follow-up drafts:** after five days with no reply, Career Atlas prepares a follow-up draft but does not send it automatically.
- **Interview mode:** interview replies create a role-specific preparation brief grounded in the job description and uploaded resume.
- **Whole-form diagnostics:** unresolved form fields are collected together so one adapter update can fix several blockers at once.
- **Sensitive-profile separation:** protected/demographic answers can only come from a separate private `SENSITIVE_PROFILE_JSON`; nothing is inferred from name, location or other profile fields.
- **Daily strategy engine:** candidate roles are diversified across backend, AI/automation, full-stack, software, data and embedded categories before application attempts.
- **Quality modes:** Conservative (85+), Balanced (75+) and Aggressive (65+) are persisted in the database and selectable from the Dashboard.
- **Live dashboard:** shows pipeline counts, top opportunity probabilities, recruiter messages, follow-up drafts and recent LangGraph events.

### Safety and submission guarantees

Career Atlas never bypasses CAPTCHA/anti-bot checks or employer assessments. It does not fabricate experience, education, legal eligibility or protected-trait answers. A job is only written as `submitted` after a confirmation phrase or confirmation URL is detected, or after the user manually records an employer confirmation.

The current production worker uses LangGraph for the score gate → email route → browser route → fallback → terminal state sequence. A real Pakistan-calendar-day cap is enforced from confirmed submissions rather than from jobs merely processed during one run.
