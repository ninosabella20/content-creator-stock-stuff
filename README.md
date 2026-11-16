# Daily Market Brief: automated media production

Turns the data that [Market Brief](https://ninosabella20.github.io/market-brief/) publishes every
morning into a ~10-minute daily news briefing:

* **Script:** market overview → one top story for each of the 11 GICS sectors → geopolitics → takeaway
* **Narration:** one consistent AI voice (Gemini TTS, free tier; Piper as an offline fallback)
* **Video:** 1080p30 H.264/AAC with animated charts, story cards, a world map, burned-in subtitles
* **Podcast (Spotify, audio only):** the same narration as an MP3, published automatically through an RSS feed
* **YouTube:** official Data API upload (off by default; private/unlisted only until you approve public)

Runs on GitHub Actions at **09:00 Madrid, Monday-Saturday**, just after Market Brief refreshes, and
covers the latest US session (Tue-Sat = Mon-Fri's close; Monday skips because Saturday already
covered Friday). Target cost: **€0/month**.

> Not investment advice. The pipeline only uses facts that Market Brief already published and checks the
> script's numbers against them, but AI-written text can still be wrong. Review episodes before enabling
> automatic public publishing.

---

## 1. What runs every morning

```
06:00-07:00 Madrid  Market Brief refreshes data.json + geo.json (separate repo, untouched): stories,
                    geopolitics, company facts
09:00 Madrid        Daily production (this repo), Mon-Sat (cron-job.org; GitHub schedule as backup)
   latest completed US session = episode ID; already covered (Monday, post-holiday) -> skip
   official closes for SPY + 11 sector ETFs + story companies (Yahoo Finance, free) cross-check
   Market Brief's numbers (if Market Brief describes an older session, its text is background only)
   rank stories per sector (rules) ── Gemini picks 1 per sector and writes the script (1 call)
   validate script (11 sectors, URLs, numbers vs source data, no advice) ── 1 repair call if needed
   narrate (Gemini TTS, ~6 calls) ── loudness-normalise (-16 LUFS) ── WAV master + MP3
   render video (parallel) + subtitles (SRT/VTT) + thumbnail + cover art
   completeness check ── publish ONLY if enabled and the episode is clean:
      YouTube (Data API)  ·  Spotify (MP3 on a GitHub Release + RSS feed on GitHub Pages)
   ledger + feed committed ── files uploaded as a GitHub Actions artifact (14 days)
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
| `PUBLISH_SPOTIFY` | `false` | `true` = add every clean episode to the podcast feed (Spotify) |
| `PODCAST_OWNER_EMAIL` | none | email shown in the feed for Spotify ownership verification |
| `SPOTIFY_MODE` | `rss` | `rss` (automatic) or `manual` (upload package only) |

A one-off test upload: **Actions** → *Daily production* → **Run workflow** → tick *force* and
*publish_youtube*, privacy *private* → **Run workflow**.

Safety rules built into the code (not just config):
* An episode is **never uploaded** if it is flagged (stale data, template script, fallback voice),
  incomplete, or a partial test render.
* An episode is **never uploaded twice**: the ledger records each upload, and the channel's latest
  uploads are checked for the episode ID before uploading.
* `public` is downgraded to `private` unless `PUBLISH_PUBLICLY=true`.

## 4. Spotify (automatic, audio only)

Spotify has no public upload API, so the official automatic route is an RSS feed that Spotify reads:

* Each clean episode's MP3 is attached to a **GitHub Release** in this repo (tag = episode ID).
* `docs/podcast/feed.xml` is rebuilt and served by **GitHub Pages** at
  `https://ninosabella20.github.io/content-creator-stock-stuff/podcast/feed.xml`.
* Your Spotify for Creators show is **redirected** to that feed once; after that Spotify picks up every
  new episode by itself, normally within a few hours. Apple Podcasts etc. can use the same feed.

One-time setup (≈10 minutes):

1. **Turn on GitHub Pages:** repo → **Settings** → **Pages** → *Build and deployment* → Source
   **Deploy from a branch** → Branch `claude/eager-ride-5heya6`, folder **/docs** → **Save**.
   After a minute the feed URL above opens in a browser.
2. **Owner email:** Spotify confirms you own the show by sending a code to the email in the feed.
   Repo → Settings → Secrets and variables → Actions → **Variables** → **New repository variable** →
   `PODCAST_OWNER_EMAIL` = the address you want (it will be **publicly visible** in the feed; a dedicated
   address is a good idea).
3. **Switch publishing on:** add the variable `PUBLISH_SPOTIFY` = `true`.
4. After the first episode is in the feed, on a computer (not the mobile app) open
   https://creators.spotify.com → **Settings** → **Redirect your podcast** → paste the feed URL →
   **Redirect**. Spotify says this can take up to 7 days to complete.

Episodes are never added to the feed if they are flagged (stale data, template script, fallback voice)
or incomplete. To pause, set `PUBLISH_SPOTIFY` to `false`. For manual uploads instead, set
`SPOTIFY_MODE` = `manual`: every run still includes a `podcast/` upload package with instructions.

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

## 7. Schedule, daylight saving and market holidays

**Main trigger: cron-job.org at 09:00 Europe/Madrid, Monday-Saturday** (GitHub's own scheduler can
start runs late). Setup:

1. GitHub: create a fine-grained token at https://github.com/settings/personal-access-tokens/new →
   *Only select repositories*: this repo → *Repository permissions* → **Actions: Read and write**.
2. cron-job.org → *Create cronjob*:
   * URL `https://api.github.com/repos/ninosabella20/content-creator-stock-stuff/actions/workflows/daily-production.yml/dispatches`
   * Schedule: custom, Monday-Saturday, 09:00; Advanced → time zone `Europe/Madrid`
   * Advanced → method **POST**; headers `Authorization: Bearer <token>`,
     `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`,
     `Content-Type: application/json`; body `{"ref":"claude/eager-ride-5heya6","inputs":{"scheduled":"true"}}`
   * Success = HTTP **204**. Turn on "notify on failure". Renew the token before it expires.

**Backup:** the workflow's own cron at 07:00, 08:00 and 09:30 UTC (Mon-Sat). The code only proceeds
between **08:55 and 13:00 Madrid** and never produces the same session twice:

| UTC trigger | Winter (CET, UTC+1) | Summer (CEST, UTC+2) |
|---|---|---|
| 07:00 | 08:00 → too early, skip | **09:00 → produce** |
| 08:00 | **09:00 → produce** | 10:00 → already done, skip |
| 09:30 | 10:30 → backup | 11:30 → backup |

Which session each run covers: Tue→Mon, Wed→Tue, Thu→Wed, Fri→Thu, **Sat→Fri**; **Monday skips**
(latest close is still Friday's). After a US market holiday, the next run skips too. The episode ID and
title use the US **session date**, e.g. `market-brief-daily-2026-11-13`. Production takes 15-20
minutes, so episodes are out around 09:20 Madrid.

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
