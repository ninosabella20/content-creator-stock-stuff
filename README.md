# Daily Market Brief: automated media production

Turns the data that [Market Brief](https://ninosabella20.github.io/market-brief/) publishes every
morning into a ~10-minute daily news briefing:

* **Script:** market overview → one top story for each of the 11 GICS sectors → geopolitics → takeaway
* **Narration:** one consistent AI voice (Gemini TTS, free tier; Piper as an offline fallback)
* **Video:** 1080p30 H.264/AAC with animated charts, story cards, a world map, burned-in subtitles
* **Podcast:** the same narration as an MP3 with ID3 tags and cover art, plus a ready-to-upload Spotify package
* **YouTube:** official Data API upload (off by default; private/unlisted only until you approve public)

Runs on GitHub Actions every day at **08:30 Europe/Madrid**. Target cost: **€0/month**.

> Not investment advice. The pipeline only uses facts that Market Brief already published and checks the
> script's numbers against them, but AI-written text can still be wrong. Review episodes before enabling
> automatic public publishing.

---

## 1. What runs every morning

```
06:00-07:00  Market Brief website refreshes data.json + geo.json (separate repo, untouched)
08:30        Daily production (this repo)
   fetch data.json + geo.json ── stale? retry every 5 min for up to 60 min, then stop (no stale episodes)
   rank stories per sector (rules) ── Gemini picks 1 per sector and writes the script (1 call)
   validate script (11 sectors, URLs, numbers vs source data, no advice) ── 1 repair call if needed
   narrate (Gemini TTS, ~6 calls) ── loudness-normalise (-16 LUFS) ── WAV master + MP3
   render video (parallel) + subtitles (SRT/VTT) + thumbnail + cover art
   completeness check ── podcast package ── publish ONLY if enabled and the episode is clean
   ledger committed (state/episodes.json) ── files uploaded as a GitHub Actions artifact (14 days)
```

**Where to find the episode:** GitHub → **Actions** → *Daily production* → latest run → **Artifacts**
(bottom of the page) → download `market-brief-daily-YYYY-MM-DD-runN`. Inside:

| File | What |
|---|---|
| `market-brief-daily-<date>.mp4` | YouTube video (1920×1080, 30 fps) |
| `market-brief-daily-<date>.mp3` | Podcast audio (128 kbps, tagged, cover embedded) |
| `narration_master.wav` | Master narration (44.1 kHz, -16 LUFS) |
| `*.srt`, `*.vtt` | Subtitles |
| `thumbnail.jpg`, `cover.jpg` | YouTube thumbnail (1280×720), podcast art (3000×3000) |
| `script.md` | The script, with the source link under each story (read this first) |
| `youtube_metadata.json` | Title, description (chapters + sources + disclaimer), tags |
| `podcast/` | Spotify upload package + `UPLOAD_TO_SPOTIFY.md` |
| `run_report.json`, `run.log` | Every stage's status, Gemini usage, any quota errors |

The run page also shows a summary table (stages, Gemini calls, YouTube status).

## 2. Credentials

| Secret (exact name) | Status | Used for |
|---|---|---|
| `GEMINI_API_KEY` | ✅ added | Script + narration (podcast project's own key; Market Brief's key is never used) |
| `YOUTUBE_CLIENT_ID` | ✅ added | YouTube OAuth |
| `YOUTUBE_CLIENT_SECRET` | ✅ added | YouTube OAuth |
| `YOUTUBE_REFRESH_TOKEN` | ❌ **needed for YouTube uploads only** | See step 2b |

**No other API keys are required.** Market Brief data is public JSON; Piper, FFmpeg, Pillow and
Matplotlib need no keys; the ledger commit and failure issues use GitHub's built-in token.

Check which secrets exist (names only; GitHub never shows values): repo → **Settings** →
**Secrets and variables** → **Actions** → *Repository secrets*.

### 2a. Make the OAuth app "In production" first (important)

While the consent screen is in **Testing**, Google expires refresh tokens after **7 days**, so daily
uploads would break every week. Publishing the app removes that limit; no Google review is needed
for your own use (you'll see an "unverified app" warning once, which is expected).

1. Open https://console.cloud.google.com/ and select the project **Podcast** (top bar).
2. Menu ☰ → **Google Auth Platform** → **Audience**.
3. Under *Publishing status* click **Publish app** → **Confirm**. Status becomes *In production*.
4. Menu ☰ → **Google Auth Platform** → **Data Access** → **Add or remove scopes** → tick
   `.../auth/youtube.upload` and `.../auth/youtube.force-ssl` (captions + duplicate check) → **Update** → **Save**.

### 2b. Generate `YOUTUBE_REFRESH_TOKEN` (once, on your computer, ~3 minutes)

Uses Google's official loopback flow for Desktop-app clients. Needs only Python 3.

```bash
git clone https://github.com/ninosabella20/content-creator-stock-stuff.git
```

```bash
cd content-creator-stock-stuff
```

```bash
python3 scripts/get_youtube_refresh_token.py
```

1. Paste the **Client ID** when asked, then the **Client Secret** (typing is hidden).
   (Find both at console.cloud.google.com → Google Auth Platform → **Clients** → your Desktop client.)
2. A browser opens. Sign in with the Google account that **owns the YouTube channel**.
   If you have several channels (brand accounts), pick the channel the show should go to.
3. "Google hasn't verified this app" → **Advanced** → **Go to Market Brief Podcast (unsafe)** → **Continue**.
4. The terminal prints the refresh token. Copy it.
5. GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret** →
   Name `YOUTUBE_REFRESH_TOKEN` → paste → **Add secret**. Clear your terminal.

Test it without uploading anything: **Actions** → *YouTube auth check* → **Run workflow**. The log prints
the channel name the uploads will go to.

### 2c. Two YouTube rules to know

* **Uploads from an un-audited API project are locked to *private*.** This applies to every Google Cloud
  project created after July 2020, including **Podcast**. Private test uploads work fine. Before public
  publishing, submit Google's free *YouTube API Services Audit and Quota Extension* form
  (https://support.google.com/youtube/contact/yt_api_form). Videos locked private before the audit
  stay private; later uploads can be public.
* **Custom thumbnails need a verified channel:** verify once at https://www.youtube.com/verify (phone).
  Until then the upload still works and the thumbnail step reports "not allowed".

Quota: the default 10,000 units/day covers one upload + thumbnail + captions (~2,050 units).

## 3. Turning publishing on (when you're happy with the episodes)

All switches are repository **Variables** (Settings → Secrets and variables → Actions → **Variables** tab):

| Variable | Default | Meaning |
|---|---|---|
| `PUBLISH_YOUTUBE` | `false` | `true` = upload every clean daily episode |
| `YOUTUBE_PRIVACY` | `private` | `private` or `unlisted` (or `public`, which ALSO needs the next one) |
| `PUBLISH_PUBLICLY` | `false` | master switch for anything public; leave `false` until you approve |
| `TTS_PROVIDER` | `gemini` | `gemini` or `piper` |
| `NARRATOR_VOICE` | `Charon` | any Gemini prebuilt voice (e.g. `Orus`, `Iapetus`, `Kore`, `Schedar`) |
| `GEMINI_MODEL` | auto | pin a text model, e.g. `gemini-2.5-flash` |

A one-off test upload: **Actions** → *Daily production* → **Run workflow** → tick *force* and
*publish_youtube*, privacy *private* → **Run workflow**.

Safety rules built into the code (not just config):
* An episode is **never uploaded** if it is flagged (stale data, template script, fallback voice),
  incomplete, or a partial test render.
* An episode is **never uploaded twice**: the ledger records each upload, and the channel's latest
  uploads are checked for the episode ID before uploading.
* `public` is downgraded to `private` unless `PUBLISH_PUBLICLY=true`.

## 4. Spotify

Spotify has **no public API for uploading episodes** (checked Nov 2026). Supported free options:

1. **Manual upload (default, ~2 min/day).** Each artifact contains `podcast/` with the MP3, cover,
   title, description and step-by-step `UPLOAD_TO_SPOTIFY.md`. Uploading the **MP4** instead of the MP3
   in Spotify for Creators makes it a **video podcast** on Spotify.
2. **Automatic via RSS (audio only on Spotify).** `src/publishing/spotify.py` already builds a standard
   podcast RSS feed. Hosting it free (GitHub Pages + MP3s on GitHub Releases) and moving the show with
   Spotify's official *"Switching away from Spotify for Creators with a 301 redirect"* process would make
   Spotify, Apple and others pick episodes up automatically, but Spotify would then show audio only.
   Not enabled: tell me if you want it.

Spotify also lets some third-party hosts deliver video via its Distribution API (Libsyn, Podigee,
Audioboom, Audiomeans, Podspace as of May 2026), but they're paid and have their own APIs, so this
project doesn't use them.

## 5. Free-tier budget

```bash
python -m src.main quota
```

| Item | Per day | Free allowance |
|---|---|---|
| Gemini text (script) | 1-2 requests | ~20 requests/day per model (as measured by Market Brief, Nov 2026) |
| Gemini TTS (narration) | ~6 requests (~300 words each) | not published by Google; shown per project in AI Studio → Rate limits. 4 TTS models are tried in order |
| Piper fallback | unlimited | runs on the GitHub runner |
| YouTube Data API | ~2,050 units | 10,000 units/day |
| GitHub Actions | ~15-25 min | unlimited for public repos |
| Artifact storage | ~250 MB per episode, kept 14 days | free for public repos |

Month: ~30 episodes, ~300 narration minutes, ~40,000 spoken words.
If a free quota runs out, the run **reports it** (run summary + `run_report.json`, with Google's quota ID)
and falls back to free alternatives that are flagged (template script / Piper voice) and never auto-published.
Nothing ever switches to a paid service.

## 6. Configuration

Defaults live in `config/settings.yaml` (word targets, voice, style prompt, pacing, video size/fps,
schedule window, freshness rules, story-age limits, quotas). Every important value can be overridden by
an environment variable / repository variable (see `.env.example`). Never put secrets in either file.

## 7. Schedule and daylight saving

GitHub cron is UTC only, so the workflow fires at **06:30, 07:30 and 09:15 UTC** and the code proceeds only
when it is between **08:30 and 13:00 in Madrid** and today's episode doesn't exist yet:

| UTC trigger | Winter (CET, UTC+1) | Summer (CEST, UTC+2) |
|---|---|---|
| 06:30 | 07:30 → too early, skip | **08:30 → produce** |
| 07:30 | **08:30 → produce** | 09:30 → already done, skip |
| 09:15 | 10:15 → backup if the earlier run was dropped | 11:15 → backup |

The switch happens automatically on DST dates. GitHub sometimes starts scheduled runs late; for a
guaranteed start you can add an external trigger the same way Market Brief does (cron-job.org calling
`workflow_dispatch`), but it isn't required.

## 8. Running locally

```bash
pip install -r requirements.txt   # plus FFmpeg: sudo apt install ffmpeg / brew install ffmpeg
```

```bash
export GEMINI_API_KEY=...   # podcast key
```

```bash
python -m src.main run --force                       # full episode -> output/<episode-id>/
```

```bash
python -m src.main run --force --script-only         # just the script (fast)
```

```bash
python -m src.main run --force --video-seconds 30    # quick partial render for design checks
```

```bash
TTS_PROVIDER=piper python -m src.main run --force --template   # fully offline, no Gemini
```

Tests (no network, no keys): `pip install pytest && python -m pytest -q`

## 9. Code map

```
src/
  main.py                 pipeline + CLI (stages, gates, ledger, report)
  config.py               settings.yaml + env overrides; secret handling/redaction
  gemini_client.py        Gemini REST: model fallback, 429 handling, call caps, usage accounting
  state.py                episode ledger (idempotency, duplicate prevention, recent stories)
  quota.py                free-tier estimator
  data/                   market_brief.py, geopolitics.py (parsers), validation.py (freshness + retries)
  editorial/              story_selection.py (rule-based ranking), script_generator.py (Gemini prompt),
                          template_writer.py (no-AI fallback), episode.py (assembly: facts from data only),
                          script_validator.py (11 sectors, numbers, advice, sign-off), pipeline.py
  audio/                  base.py (provider interface), gemini_tts.py, piper_tts.py, text_normalize.py,
                          narration.py (chunking, single-voice guarantee, timeline), audio_processor.py
  video/                  graphics.py, charts.py (Pillow + Matplotlib), scenes.py, renderer.py (parallel
                          FFmpeg), subtitles.py, thumbnail.py (thumbnail + cover)
  publishing/             metadata.py, youtube.py (resumable upload), spotify.py (package + RSS)
scripts/get_youtube_refresh_token.py   one-time OAuth helper
.github/workflows/        daily-production.yml, youtube-auth-check.yml, tests.yml
```

To add a voice engine, subclass `TTSProvider` (`src/audio/base.py`) and register it in
`make_provider()` (`src/audio/narration.py`). To add a podcast host, subclass `PodcastPublisher`.

## 10. Licences and content rules

* Fonts: Inter (SIL OFL 1.1, `assets/fonts/OFL.txt`). Flags: flag-icons (MIT). Map: Natural Earth (public domain).
* Gemini TTS voices are Google prebuilt voices (no cloning of real people). Gemini API output terms apply.
* Piper is GPL-3.0 and is run as a separate program. The default Piper voice is trained on LibriTTS
  (CC BY 4.0): commercial use is fine with attribution ("Fallback voice: Piper, LibriTTS dataset").
  Check the voice's MODEL_CARD if you change it.
* No news images, video or full articles are used: only headlines, short facts and links, with original
  sources credited in every description. Company logos are off (`video.show_company_logos: false`).
* Music is off until properly licensed audio is available.
* YouTube uploads set `containsSyntheticMedia: true` (AI narration disclosure).

## 11. Troubleshooting

| Symptom | Fix |
|---|---|
| Issue "Production failed: … (validate_data)" | Market Brief didn't refresh today. Check its Actions tab; re-run this workflow afterwards |
| Run summary shows Gemini 429 with `PerDay` | Free daily quota used up; tomorrow resets (midnight Pacific). The episode used the flagged fallback |
| `invalid_grant` from YouTube | Refresh token expired/revoked: do 2a, then 2b again |
| Thumbnail "not allowed (403)" | Verify the channel at youtube.com/verify |
| Workflow stopped running | GitHub disables schedules after 60 days without commits; the daily ledger commit prevents this. Actions → *Daily production* → **Enable workflow** |
