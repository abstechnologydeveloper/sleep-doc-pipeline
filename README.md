# Sleep Story Video Pipeline

This project generates a complete narrated sleep-story video:

1. Gemini writes the narration script.
2. Gemini TTS creates the voice narration.
3. Cloudflare Workers AI FLUX.1 Schnell creates the scene images.
4. FFmpeg assembles the images, transitions, captions, and audio into an MP4.

It also includes a Dockerized admin dashboard for automatic generation,
manual video uploads, scheduling, and multi-platform publishing status.

## Admin dashboard

The dashboard provides two workflows:

- **Automatic:** keep the default duration and leave the content fields blank
  to generate the topic, title, description, hashtags, and complete video, then
  optionally queue it for selected platforms.
- **Manual:** upload an existing MP4/MOV, add post metadata, and queue it for
  YouTube, Facebook, Instagram, and TikTok.

Job status and completed-video availability update in the dashboard through an
authenticated WebSocket connection, without requiring a page refresh.

Copy `.env.example` to `.env`, set the API keys and a strong session secret,
then start the dashboard:

```bash
docker compose up -d --build
```

The production dashboard is available at
`https://sleep-studio.69.197.164.87.nip.io`. Nginx terminates HTTPS and proxies
to port `8090`, which is bound to localhost so it cannot bypass TLS. Use
`COOKIE_SECURE=true`. SQLite state, uploads, and generated media are
bind-mounted so container replacement does not erase them.

Social posting uses official platform APIs. Each connector remains in
`waiting_for_connections` until its approved OAuth app and account identifiers
are configured. TikTok requires Content Posting API approval; Instagram
requires a Professional account linked to a Facebook Page.

### GitHub Actions deployment

Production deployment runs only through `.github/workflows/deploy.yml` on a
dedicated self-hosted runner carrying the `sleep-studio` label. Add a repository
secret named `STUDIO_ENV_FILE` containing the complete production `.env` file.
If that secret is not configured, deployment preserves and uses the existing
`.env` file in the VPS deployment directory.

The workflow runs only when manually started from the GitHub Actions page. It
validates the Python source, deploys with Docker Compose, checks `/health`, and
removes unused Docker images/build cache older than 24 hours. It never prunes
Docker volumes, and the deployment script excludes the database, uploads,
generated scripts, audio, images, and videos from rsync deletion.

## Requirements

- Python 3.12 with the existing project dependencies
- FFmpeg available on `PATH`
- A Gemini API key
- A Cloudflare account with Workers AI access

FLUX.1 Schnell is a hosted diffusion model. Cloudflare includes 10,000 free
AI Neurons per day; usage above that requires the Workers Paid plan. Current
model pricing is documented at
https://developers.cloudflare.com/workers-ai/platform/pricing/.

Add the keys to `.env`:

```env
GEMINI_API_KEY=your_gemini_key
CLOUDFLARE_ACCOUNT_ID=your_cloudflare_account_id
CLOUDFLARE_API_TOKEN=your_workers_ai_api_token
```

Do not commit `.env` or share its contents.

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
assemble_video.py    Stable video-assembly entry point

data/ scripts/ audio/ images/ videos/   Persistent runtime output
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

The duration is specified in minutes. The pipeline runs script, audio, images,
and video assembly in order.

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

The current pacing is one image per 50 words, approximately six images for a
two-minute narration. Existing scene files are skipped so interrupted runs can
continue without paying for the same image twice.

Output:

```text
images/<script-name>/scene_001.jpg
images/<script-name>/scene_002.jpg
...
```

### 4. Assemble the final video

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
- Gentle crossfade transitions
- Burned-in captions
- H.264 video
- AAC narration audio
- Fast-start MP4 metadata

If fewer images exist than expected, the available images are distributed
evenly instead of failing.

Output:

```text
videos/<script-name>.mp4
```

## Output folders

```text
scripts/  Generated narration text
audio/    Final WAV narration and measured caption timing data
images/   Scene images grouped by script
videos/   Finished captioned MP4 videos
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
