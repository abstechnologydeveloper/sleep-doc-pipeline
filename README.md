# My Automation Studio

My Automation Studio helps creators turn a story idea or a complete written story into a reviewed video for YouTube and other social platforms.

The application creates scripts, scene plans, images, narration, sound, captions, thumbnails, and MP4 videos. Creators approve each important stage before the next paid stage begins.

All generated scene images and thumbnails use a colorful animated storybook/cartoon medium. Human characters are stylized illustrations, never photorealistic people or live-action frames.

## Story workflow

1. Enter an idea or paste a complete story.
2. Review and edit the script.
3. Review the storyboard before images are generated.
4. Review or regenerate individual scene images.
5. Approve the thumbnail.
6. Approve narration, sound effects, and ambience.
7. Assemble and review the final video.
8. Download it or publish it to a connected YouTube channel.

A pasted story is preserved exactly until the creator edits it. Global profile settings do not change a story or its pictures. Failed jobs retain reusable work so a retry does not unnecessarily repeat completed stages.

## Main services

- FastAPI and Uvicorn web application
- PostgreSQL database
- Gemini for story and metadata generation
- Cloudflare Workers AI for scene images
- Cloudflare R2 for private media storage
- AI33.Pro for narration, speech-timed captions, sound effects, and instrumental music
- FFmpeg for captions, movement, transitions, audio, and final assembly
- Resend and Google for passwordless sign-in
- Paystack for subscriptions
- YouTube Data API v3 for channel publishing

## Local development

Requirements:

- Docker
- Docker Compose
- Colima or Docker Desktop running
- A populated `.env` file

Create the local environment file once:

```bash
cp .env.example .env
```

Add the required credentials to `.env`. Never commit or share that file.

### First local start

Use the local override for live server reload and visible logs:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up --build
```

Open http://127.0.0.1:8090.

### Later starts

The image does not need to be rebuilt after ordinary Python, HTML, CSS, or JavaScript changes:

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml up
```

Rebuild only after changing `Dockerfile`, `requirements.txt`, or installed system packages. Press `Ctrl+C` to stop the foreground process.

### Local health check

```bash
curl http://127.0.0.1:8090/health
```

## Configuration

Use `.env.example` as the authoritative list. The main groups are:

- AI: `GEMINI_API_KEY`, `CLOUDFLARE_*`, and `AI33_API_KEY`
- Database: `POSTGRES_PASSWORD` and `DATABASE_URL`
- Authentication: `ADMIN_SESSION_SECRET`, `AUTH_IP_HASH_SALT`, `RESEND_API_KEY`, and `GOOGLE_OAUTH_*`
- Storage: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, and `R2_BUCKET`
- Billing: `PAYSTACK_SECRET_KEY`
- Application: `PUBLIC_BASE_URL`, `COOKIE_SECURE`, `ADMIN_EMAIL`, and `SUPPORT_EMAIL`

Image generation tries AI33.Pro Seedream first, then Cloudflare Flux, and finally
Leonardo Lucid Origin. The two Cloudflare models share one account allowance.

Use `COOKIE_SECURE=false` and a localhost `PUBLIC_BASE_URL` only for local testing. Production and staging must use HTTPS and secure cookies.

## External callbacks

Production Google OAuth redirect URIs:

```text
https://myautomationstudio.com/auth/google/callback
https://myautomationstudio.com/connections/youtube/callback
```

Paystack webhook:

```text
https://myautomationstudio.com/billing/webhook
```

Staging must use the staging domain, separate OAuth callbacks where applicable, a Paystack test key, and an isolated database and R2 prefix.

## Storage and data safety

- PostgreSQL data is stored in the `postgres_data` Docker volume.
- Completed private media is stored in Cloudflare R2.
- Rendering folders are mounted for recovery and resumable jobs.
- Deployment cleanup must never remove the PostgreSQL volume or creator media.
- Keep `OAUTH_TOKEN_ENCRYPTION_KEY` stable or connected YouTube accounts must reconnect.

## Deployment

Production deploys from `main`; staging deploys from `staging`.

Repository secrets required by GitHub Actions:

- `PRODUCTION_STUDIO_ENV_FILE`
- `STAGING_STUDIO_ENV_FILE`

Each secret must contain a multiline environment file with one `KEY=value` entry per line. Do not store the environment file under GitHub Variables.

Production requires a Paystack `sk_live_...` key. Staging requires a Paystack `sk_test_...` key.

See [DEPLOYMENT.md](DEPLOYMENT.md) for VPS, DNS, Nginx, TLS, GitHub Actions, and environment-isolation instructions.

## Project layout

```text
src/backend/       FastAPI routes, database, worker, billing, and publishing
src/pipeline/      Script, audio, image, sound, and video pipeline
src/web/           Templates and static assets
docker-compose.yml Production-like application services
docker-compose.local.yml Local live-reload override
data/               Application recovery data
scripts/            Generated and reviewed scripts
audio/ images/ sounds/ thumbnails/ videos/  Rendering and recovery files
```

## Troubleshooting

### The browser says connection refused

The containers are not running or the application exited. Read the terminal output and confirm Uvicorn is listening on port `8090`.

### View local logs

```bash
docker compose -f docker-compose.yml -f docker-compose.local.yml logs -f studio
```

### A generation stage fails

Do not delete completed artifacts. Correct the reported credential, provider, or content error, then retry the job so reusable stages can resume.

### YouTube connection is blocked

Enable YouTube Data API v3, register the exact callback URL, and ensure the Google OAuth app is published or the user is an approved tester.

## Additional documentation

- [BUSINESS_RULES.md](BUSINESS_RULES.md) — plans, limits, accounts, and billing behavior
- [DEPLOYMENT.md](DEPLOYMENT.md) — production and staging operations
- [COST_MODEL.md](COST_MODEL.md) — estimated generation and storage costs
- [TEST_TOPICS.md](TEST_TOPICS.md) — low-cost test ideas and checks
