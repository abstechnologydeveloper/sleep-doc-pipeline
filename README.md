# Sleep Story Video Pipeline

This project generates a complete narrated sleep-story video:

1. Gemini writes the narration script.
2. Gemini TTS creates the voice narration.
3. Cloudflare Workers AI Leonardo Lucid Origin creates native widescreen scene images.
4. ElevenLabs optionally creates sparse, story-specific sound effects.
5. FFmpeg adds cinematic movement, contextual effects, transitions, captions,
   narration, and quiet sound effects, then creates a dedicated thumbnail.

It also includes a Dockerized multi-tenant creator application for automatic
generation, manual video uploads, scheduling, and multi-platform publishing status.

## Accounts and creator workspaces

The public landing page is available at `/`, and each signed-in creator uses the
private workspace at `/app`. Authentication is passwordless: creators can request
a 15-minute, one-use email link or continue with Google. Every new job and uploaded
video belongs to the creator who made it; creators cannot access another account's
work through pages, media URLs, WebSockets, or job actions.

Set `ADMIN_EMAIL` to the administrator's email address. That account receives the
administrator role only after the address is verified through email or Google. The
administrator-only **Customers** workspace lists all accounts and allows free-tier
monthly story limits, maximum story duration, and account status to be changed.
The administrator is administration-only and onboards normal creator accounts from
the **Customers** workspace. Creators then verify their email or use Google to sign in.
New creators default to three stories per rolling 30 days, five minutes per story, and one
active generation at a time. The complete policy is in `BUSINESS_RULES.md`.

Passwordless authentication requires these production settings:

```env
ADMIN_EMAIL=admin@example.com
ADMIN_SESSION_SECRET=replace_with_at_least_32_random_bytes
AUTH_IP_HASH_SALT=replace_with_an_independent_random_value
AUTH_FROM_EMAIL=Sleep Studio <login@your-verified-domain.example>
RESEND_API_KEY=your_resend_api_key
GOOGLE_OAUTH_CLIENT_ID=your_google_client_id
GOOGLE_OAUTH_CLIENT_SECRET=your_google_client_secret
PUBLIC_BASE_URL=https://myautomationstudio.com
COOKIE_SECURE=true
POSTGRES_PASSWORD=replace_with_a_long_random_password
DATABASE_URL=postgresql://sleep_studio:replace_with_a_long_random_password@postgres:5432/sleep_studio
OAUTH_TOKEN_ENCRYPTION_KEY=replace_with_at_least_32_random_bytes
R2_ACCOUNT_ID=your_cloudflare_account_id
R2_ACCESS_KEY_ID=your_r2_access_key_id
R2_SECRET_ACCESS_KEY=your_r2_secret_access_key
R2_BUCKET=your_private_r2_bucket
R2_PREFIX=sleep-studio
R2_PUBLIC_DOMAIN=pub-24bed8d26f7f4b77aac16769cc765325.r2.dev
```

Register these production Google OAuth redirect URIs:
`https://myautomationstudio.com/auth/google/callback` and
`https://myautomationstudio.com/connections/youtube/callback`.

## Application workspaces

Creators use a compact sidebar with five workspaces:

- **Overview:** production counts, recent jobs, finished media, and connector status.
- **Storytelling:** generate complete narrated videos for the shared media library.
- **Social Posts:** upload finished media or publish a library video with shared metadata.
- **Jobs:** filter and manage generation, media-upload, and publishing jobs.
- **Settings & billing:** edit the creator profile and niche, choose narration defaults,
  switch plans, and review Paystack-funded access.

Administrators see only the operational overview, all jobs, and customer management;
creator generation, uploads, publishing forms, and channel connections are denied by
the server as well as hidden from the navigation.

Job status, overview counts, and newly completed media update through an authenticated
WebSocket connection. Publishing uses a server-validated media ID rather than accepting
an arbitrary file path from the browser.

Each creator sees job numbers beginning at `#1`; these stable creator-local numbers are
separate from the globally unique IDs used internally. The interface defaults to light
mode and stores an optional dark-mode preference in the browser. The public landing page
and finished-video share pages include crawler-friendly Open Graph preview images for
WhatsApp and other social platforms.

Story generation explicitly requires a central character or focal subject, an early story
question, cause-and-effect progression, meaningful middle changes, stable continuity,
setup/payoff discipline, a character-driven climax, and a complete gentle resolution.
Metadata and scene planning support the same promise so the title, thumbnail, narration,
visuals, and ending do not drift into different stories.

Automatic jobs retain their exact saved script path. Retrying a failed job resumes that
script and reuses completed narration chunks and generated assets instead of starting over.

Copy `.env.example` to `.env`, set the API keys and a strong session secret,
then start the dashboard:

```bash
docker compose up -d --build
```

The production dashboard is available at `https://myautomationstudio.com`, and the
isolated staging dashboard is available at `https://staging.myautomationstudio.com`.
Nginx terminates HTTPS and proxies
to port `8090`, which is bound to localhost so it cannot bypass TLS. Use
`COOKIE_SECURE=true`. PostgreSQL uses the private `postgres_data` Docker volume.
Pipeline working directories remain bind-mounted only for rendering and failed-job
recovery; creator uploads, completed videos, and thumbnails are stored privately in
Cloudflare R2. On the first PostgreSQL startup,
the application imports the existing `data/admin.sqlite3` records once and leaves
the source file untouched as a recovery copy.

### Cloudflare R2 media storage

Create an R2 API token restricted to the Sleep Studio bucket and configure the five
`R2_*` variables above. Do not copy hardcoded credentials from another repository;
rotate any credential that has previously been committed to source control.

Uploads stream directly to R2. Generated videos and thumbnails move to R2 after a
successful render, and the corresponding local video, thumbnail, audio, scene-image,
and sound-effect files are removed. Failed generations retain working files so retry
can resume safely. Existing finished local media migrates to R2 gradually while the
background worker is idle. Private videos are delivered through authenticated routes
or revocable public share tokens; the bucket does not need public access.

Social posting uses official platform APIs. Each connector remains in
`waiting_for_connections` until its approved OAuth app and account identifiers
are configured. TikTok requires Content Posting API approval; Instagram
requires a Professional account linked to a Facebook Page.

### Creator YouTube connections

Each non-admin creator connects their own YouTube channel from **Social Posts → Connections**.
Tokens are encrypted in PostgreSQL with `OAUTH_TOKEN_ENCRYPTION_KEY`; never change
that key without reconnecting every channel. YouTube uses the same global
`GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET` configured for sign-in;
individual creators never enter application client credentials.

Enable YouTube Data API v3 and register this redirect URI:
`https://myautomationstudio.com/connections/youtube/callback`.
Creators choose Public, Unlisted, or Private for each YouTube upload. Public is
preselected. YouTube quota, channel
eligibility, Google app verification, and upload-audit restrictions still apply.

### GitHub Actions deployment

Production deployment runs through `.github/workflows/deploy.yml`, while staging uses
`.github/workflows/deploy-staging.yml`, on the dedicated self-hosted runner carrying the
`sleep-studio` label. Create GitHub environments named `production` and `staging`, each
with an environment secret named `STUDIO_ENV_FILE` containing its complete `.env` file.
Deployment stops before changing containers if its environment secret is missing or belongs
to the other environment.

Production deploys from `main`; staging deploys from `staging`; both also support a manual
workflow run. Each workflow validates the Python source, deploys with Docker Compose,
checks `/health`, and
removes unused Docker images/build cache older than 24 hours. It never prunes
Docker volumes, and the deployment script excludes the database, uploads,
generated scripts, audio, images, sounds, thumbnails, and videos from rsync deletion.
See `DEPLOYMENT.md` for DNS, Nginx, TLS, OAuth, Paystack, environment isolation, and
the one-time VPS configuration.

## Requirements

- Python 3.12 with the existing project dependencies
- FFmpeg available on `PATH`
- A Gemini API key
- A Cloudflare account with Workers AI and private R2 access
- An optional ElevenLabs API key for footsteps, movement, environment, and transition sounds

FLUX.1 Schnell is a hosted diffusion model. Cloudflare includes 10,000 free
AI Neurons per day; usage above that requires the Workers Paid plan. Current
model pricing is documented at
https://developers.cloudflare.com/workers-ai/platform/pricing/.

Add the keys to `.env`:

```env
GEMINI_API_KEY=your_gemini_key
CLOUDFLARE_ACCOUNT_ID=your_cloudflare_account_id
CLOUDFLARE_API_TOKEN=your_workers_ai_api_token
CLOUDFLARE_IMAGE_MODEL=@cf/leonardo/lucid-origin
MAX_STORY_IMAGES=48
ELEVENLABS_API_KEY=your_optional_elevenlabs_key
PAYSTACK_SECRET_KEY=sk_test_or_live_key
SUPPORT_EMAIL=support@example.com
MAX_UPLOAD_MB=2048
```

Do not commit `.env` or share its contents.

Configure the Paystack webhook URL as `https://your-host/billing/webhook`. Sleep Studio
initializes NGN transactions with a unique server-side reference, verifies successful
callbacks against Paystack, and validates webhook signatures with `PAYSTACK_SECRET_KEY`.
Paid access lasts 30 days and is renewed manually; no Paystack plan code is required.

Completed storytelling usage is recorded separately from job rows, so deleting completed
work does not restore allowance. Failed and cancelled generation releases allowance. Creators
can review payment history, job and billing notifications, export their account record, or
permanently delete their creator account and stored media. Administrators can search customers,
review recent payments, and see an audit trail for limit changes.

The public privacy, terms, acceptable-use, copyright, billing and support pages are operational
policy drafts. Have qualified Nigerian legal counsel review them before accepting production
payments. See `COST_MODEL.md` before treating the published tiers as proven profitable prices.

## Project layout

```text
src/
  pipeline/   Script, audio, image, video, and orchestration implementation
  backend/    FastAPI server, database, worker, and publishing integrations
  web/        Templates and static assets
  project_paths.py   Shared locations for persistent and generated files

run_pipeline.py      Stable command-line entry point
generate_script.py   Stable script-generation entry point
generate_audio.py    Stable narration entry point
generate_images.py   Stable image-generation entry point
generate_sounds.py   Optional story sound-effects entry point
assemble_video.py    Stable video-assembly entry point

data/ scripts/   Persistent application and resumable-script data
audio/ images/ sounds/ videos/ thumbnails/   Temporary rendering and recovery data
```

Implementation code lives under `src/`. The small root entry points preserve
the existing terminal commands and keep runtime output outside the source tree.

This repository's `venv` is currently an empty Python 3.14 environment. Use the
configured Python 3.12 interpreter in the commands below:

```bash
PYTHON=/Users/apple/.pyenv/versions/3.12.0/bin/python
```

## Run the complete pipeline

Interactive mode asks for the topic and duration:

```bash
$PYTHON run_pipeline.py
```

Provide the topic and duration directly:

```bash
$PYTHON run_pipeline.py "A forgotten lighthouse keeper" 2
```

The duration is specified in minutes. The pipeline runs script, narration, images,
optional sound effects, and video assembly in order. If `ELEVENLABS_API_KEY` is not
configured, the sound step reports that it was skipped and the video still completes.

If a run stops after creating the script or audio, resume the newest project:

```bash
$PYTHON run_pipeline.py --resume
```

Resume mode skips an existing audio file, continues missing images, and rebuilds
the final video without generating another script.

## Run each stage separately

### 1. Generate the script

Interactive topic and duration:

```bash
$PYTHON generate_script.py
```

Provide both values directly:

```bash
$PYTHON generate_script.py "A quiet train crossing a sleeping country" --minutes 2
```

Output:

```text
scripts/<timestamp>_<topic>.txt
```

### 2. Generate the narration audio

Choose a saved script interactively:

```bash
$PYTHON generate_audio.py
```

Or provide its path:

```bash
$PYTHON generate_audio.py scripts/<script-name>.txt
```

Audio chunks are generated concurrently and stitched in order. The temporary
chunk folder is deleted only after the final WAV is created successfully.

Output:

```text
audio/<script-name>.wav
```

### 3. Generate the scene images

Choose a script interactively:

```bash
$PYTHON generate_images.py
```

Or provide its path:

```bash
$PYTHON generate_images.py scripts/<script-name>.txt
```

Gemini first divides the complete narration into meaningful story beats. A new
visual is planned for changes in action, place, time, character, emotion, clues,
decisions, discoveries, and other important story events. The image count is not
calculated from video duration or a fixed words-per-image ratio. Calm continuous
passages may deliberately reuse an earlier visual with different movement, while
every meaningful storyline remains represented.

The saved versioned scene plan includes exact narration word boundaries, a project
style profile, recurring character/location/prop continuity, scene prompts, camera
directions, transitions, sound cues, and a separate thumbnail concept. Cloudflare
then renders the distinct scene images. Existing files are skipped, and deliberately
reused scenes do not create another paid image request. Video timing and sound cues
read the same scene plan, keeping each visual aligned with its actual narration.

`MAX_STORY_IMAGES` is a command-line safety fallback with a default of 48. Web jobs
snapshot the creator plan's lower per-story allowance (8, 16, 32, or 48). The planner
keeps every timed story beat but consolidates compositions and reuses suitable visuals
with different motion when another paid image would exceed the allowance.

Lucid Origin receives a native 1536×864 request. The assembler scales and crops to
the final 1280×720 frame without letterboxing. A separate native-widescreen thumbnail
composition is generated for every new project.

Lucid Origin is the quality default and is paid per generated tile and step. To
restore the lower-cost fixed-format model, set
`CLOUDFLARE_IMAGE_MODEL=@cf/black-forest-labs/flux-1-schnell`.

Output:

```text
images/<script-name>/scene_001.jpg
images/<script-name>/scene_002.jpg
...
images/<script-name>/scene_plan.json
images/<script-name>/thumbnail_source.jpg
```

Older saved projects keep their original fixed scene plan and remain resumable.
New projects use dynamic scene-plan version 2.

### 4. Generate optional sound effects

Add `ELEVENLABS_API_KEY` to `.env`, then run:

```bash
$PYTHON generate_sounds.py scripts/<script-name>.txt
```

Gemini selects a small number of meaningful footsteps, doors, movement, weather,
nature, magic, or transition moments in `scene_plan.json`. Cues use stable scene IDs,
so their placement remains correct when visuals are reused. ElevenLabs generates short
MP3 cues, and interrupted runs reuse completed files. Sound generation is usage-billed;
the current API charges 40 credits per generated second when duration is specified. The
pipeline allows no more than one cue per two scenes and caps every project at 30 cues.
Official API reference: https://elevenlabs.io/docs/api-reference/text-to-sound-effects/convert

Output:

```text
sounds/<script-name>/cue_001.mp3
sounds/<script-name>/sound_manifest.json
```

### 5. Assemble the final video

Choose a script interactively:

```bash
$PYTHON assemble_video.py
```

Or provide its path:

```bash
$PYTHON assemble_video.py scripts/<script-name>.txt
```

The assembler adds:

- 1280×720 output
- Slow cinematic zoom and alternating pan
- Story-directed camera movement, color grading, transitions, and atmosphere
- Subtle rain, snow, stars, embers, or motes only when the scene calls for them
- Short five-word burned-in captions with a translucent background
- A matching `.srt` subtitle file for YouTube accessibility
- Consistent narration loudness and gentle frequency cleanup
- Sparse story sounds mixed quietly beneath narration with short fades and peak limiting
- H.264 video
- AAC narration audio
- Fast-start MP4 metadata
- A separate high-contrast 1280×720 JPEG thumbnail with a short title
- A colorful curiosity-focused thumbnail composition with a truthful 2-5 word hook

If fewer images exist than expected, the available images are distributed
evenly instead of failing.

Output:

```text
videos/<script-name>.mp4
thumbnails/<script-name>.jpg
```

## Output folders

```text
scripts/  Generated narration text
audio/    Final WAV narration and measured caption timing data
images/   Scene images grouped by script
sounds/   Optional generated effects and their timing manifest
videos/   Finished captioned MP4 videos
thumbnails/ Dedicated 16:9 video thumbnails
```

## Testing

Suggested topics and a validation checklist are available in
[TEST_TOPICS.md](TEST_TOPICS.md).

Start with a one-minute test to limit API costs:

```bash
$PYTHON run_pipeline.py "A lighthouse keeper notices one missing star" 1
```

## Troubleshooting

### `ModuleNotFoundError: dotenv`

The empty Python 3.14 `venv` is active. Run commands with the explicit Python
3.12 interpreter shown above.

### Cloudflare authentication error

In the Cloudflare dashboard, open **Workers AI**, select **Use REST API**, and
create a Workers AI API token. Copy the displayed Account ID and token into
`.env`, then rerun with `--resume`.

### Video plays without sound

Confirm that macOS is using the intended output device. The generated MP4
contains an AAC track when assembly succeeds. Restart a stale Core Audio service
with:

```bash
sudo killall coreaudiod
```

Then select **MacBook Pro Speakers** in Control Center and try playback again.

### A paid stage fails

Do not delete completed files. Fix the reported issue and use:

```bash
$PYTHON run_pipeline.py --resume
```

Existing audio and images are reused where possible.
