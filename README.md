# Career Atlas — Flutter and FastAPI

A new project for Asad's career assistant. This is separate from the earlier local n8n/SQLite dashboard.

## What works

- Flutter UI for web and Android: login, company boards, live listings, PDF CV/Resume upload, AI application preparation, official application links, and a review queue.
- FastAPI API with PostgreSQL persistence on Render (SQLite for local development), one-user password login, PDF text extraction, Greenhouse and Lever public company job feeds, deduplication, Gemini match rationale and a cover note.
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

To use **AI match & draft**, upload a text-selectable PDF under **Setup → Resume** or **CV**, then put your Gemini API key in `backend/.env` as `GEMINI_API_KEY=...` and restart the backend. Without a document, the Jobs screen offers an upload button. Without an API key, the backend returns a setup error. Job status tracking works without either.

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
