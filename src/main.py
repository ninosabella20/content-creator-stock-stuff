"""Market Brief Daily: produce (and optionally publish) one episode.

Usage:
  python -m src.main run [--date YYYY-MM-DD] [--scheduled] [--force] [--video-seconds N]
                         [--skip-video] [--template] [--data-dir DIR]
  python -m src.main quota          # free-tier usage estimate
  python -m src.main check-secrets  # which secrets are configured (names only)
  python -m src.main youtube-auth-check   # verify the YouTube refresh token works (no upload)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
from datetime import date, datetime
from pathlib import Path

from .config import ROOT, SECRET_NAMES, Settings, load_settings, secret
from .gemini_client import Usage
from .util import StageError, local_now, now_utc, setup_logging, write_json

log = logging.getLogger("market-brief-daily")


# ----------------------------------------------------------------------------- helpers
def in_window(settings: Settings, now: datetime) -> bool:
    s = settings.get_path
    start = datetime.strptime(s("schedule.local_start", "08:30"), "%H:%M").time()
    end = datetime.strptime(s("schedule.local_end", "13:00"), "%H:%M").time()
    return start <= now.time() <= end


def script_markdown(ep: dict) -> str:
    out = [f"# {ep['episode_id']}", "", f"Generator: {ep['generator']} · {ep['word_count']} words · "
           f"~{ep['estimated_minutes']} min", ""]
    for f in ep.get("flags", []):
        out.append(f"> ⚠ {f}")
    for seg in ep["segments"]:
        out += [f"## {seg['title']}", "", seg.get("narration", ""), ""]
        st = seg.get("story")
        if st:
            out += [f"*Source:* [{st['headline']}]({st['url']}) ({st.get('source')}, {st.get('published')})", ""]
        for src in seg.get("sources", []):
            out.append(f"*Source:* [{src.get('title')}]({src.get('url')}) ({src.get('publisher')})")
    val = ep.get("validation") or {}
    if val.get("warnings"):
        out += ["", "## Validator warnings", ""] + [f"- {w}" for w in val["warnings"]]
    return "\n".join(out) + "\n"


class Run:
    """Keeps the run report: every stage's status, timing and error, saved even on failure."""

    def __init__(self, out_dir: Path, episode_id: str):
        self.out = out_dir
        self.report = {"episode_id": episode_id, "started_at": now_utc().isoformat(timespec="seconds"),
                       "github_run": os.environ.get("GITHUB_RUN_ID"), "stages": [], "status": "running"}

    def stage(self, name: str, fn, *a, **k):
        t0 = time.time()
        log.info("=== stage: %s", name)
        try:
            res = fn(*a, **k)
        except StageError as e:
            self.report["stages"].append({"stage": e.stage, "status": "failed", "error": e.message[:2000],
                                          "seconds": round(time.time() - t0, 1)})
            raise
        except Exception as e:  # noqa: BLE001
            self.report["stages"].append({"stage": name, "status": "failed", "error": f"{type(e).__name__}: {e}"[:2000],
                                          "trace": traceback.format_exc()[-3000:], "seconds": round(time.time() - t0, 1)})
            raise StageError(name, f"{type(e).__name__}: {e}") from e
        self.report["stages"].append({"stage": name, "status": "ok", "seconds": round(time.time() - t0, 1)})
        return res

    def save(self) -> None:
        self.report["finished_at"] = now_utc().isoformat(timespec="seconds")
        write_json(self.out / "run_report.json", self.report)


# ---------------------------------------------------------------------------------- run
def cmd_run(args) -> int:
    settings = load_settings()
    s = settings.get_path
    tz = s("schedule.timezone", "Europe/Madrid")
    now_local = local_now(tz)
    today = now_local.date()
    gh_out = os.environ.get("GITHUB_OUTPUT")

    def gh_set(**kv):
        if gh_out:
            with open(gh_out, "a") as f:
                for k, v in kv.items():
                    f.write(f"{k}={v}\n")

    # ---- gates for scheduled runs (US market close, weekdays) --------------------------
    if args.scheduled:
        if today.weekday() not in (s("schedule.run_days") or [0, 1, 2, 3, 4, 5]):
            print(f"{today:%A}: not a production day, nothing to do.")
            gh_set(produced="false")
            return 0
        if not in_window(settings, now_local):
            print(f"Not due: it is {now_local:%H:%M} in {tz}; production window is "
                  f"{s('schedule.local_start')}-{s('schedule.local_end')}. Nothing to do.")
            gh_set(produced="false")
            return 0

    # ---- which market session is this episode about? -------------------------------------
    close_rows, session = None, None
    use_close = bool(s("sources.use_live_close", True)) and not args.data_dir
    if use_close:
        from .data.market_close import latest_session
        try:
            close_rows, session = latest_session()
        except Exception as e:  # noqa: BLE001
            print(f"FAILED at stage 'market_close': {e}")
            gh_set(produced="false", failed_stage="market_close")
            return 1
        if args.scheduled and s("schedule.require_session_today", False) and session != today:
            print(f"US market did not trade today ({today}); latest session is {session}. Nothing to do.")
            gh_set(produced="false")
            return 0
        episode_date = date.fromisoformat(args.date) if args.date else session
    else:
        episode_date = date.fromisoformat(args.date) if args.date else today
    # Market Brief freshness is judged against the day we run, not the session date
    data_date = today if use_close else episode_date

    from .editorial.episode import episode_id as make_id
    from .state import Ledger
    eid = make_id(settings, episode_date)
    ledger = Ledger(ROOT / s("storage.ledger_path", "state/episodes.json"))
    gh_set(episode_id=eid)
    if ledger.is_produced(eid) and not args.force:
        print(f"{eid} (latest US session) was already produced ({ledger.get(eid).get('status')}): "
              "nothing new to cover. Use --force to rebuild.")
        gh_set(produced="false")
        return 0

    out_dir = Path(s("storage.output_dir", "output"))
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir = out_dir / eid
    out_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(logfile=out_dir / "run.log")
    log.info("producing %s (local time %s %s)", eid, now_local.strftime("%Y-%m-%d %H:%M"), tz)
    run = Run(out_dir, eid)
    usage_text, usage_tts = Usage(), Usage()
    ledger.update(eid, date=episode_date.isoformat(), status="running", run_id=os.environ.get("GITHUB_RUN_ID"))
    ledger.save()
    files: dict[str, Path] = {}
    try:
        # 1-3. data
        from .data.validation import fetch_validated
        if args.data_dir:
            from .data.geopolitics import parse_geo
            from .data.market_brief import parse_market_brief
            dd = Path(args.data_dir)
            mb_raw = json.loads((dd / "data.json").read_text())
            geo_raw = json.loads((dd / "geo.json").read_text())
            mb, geo, rep = run.stage("fetch_data", fetch_validated, settings, data_date,
                                     sleep=lambda _: None, fetch_mb=lambda _: parse_market_brief(mb_raw),
                                     fetch_g=lambda _: parse_geo(geo_raw))
        else:
            mb, geo, rep = run.stage("fetch_data", fetch_validated, settings, data_date)
        if close_rows is not None:
            from .data.market_close import apply_close, stock_day_moves

            def close_stage():
                apply_close(mb, close_rows, session)
                stories = [st for sec in mb.stories.values() for st in sec.stories]
                moves = stock_day_moves([st.ticker for st in stories])
                for st in stories:
                    st.today_return = moves.get(st.ticker)
                return {"session": mb.as_of_label, "stock_moves": len(moves),
                        "morning_text_was_previous_session": bool(mb.prior_session)}
            run.report["market_close"] = run.stage("market_close", close_stage)
        run.report["data"] = rep.as_dict()

        # 4-6. stories + script
        from .editorial.pipeline import produce_script
        recent = ledger.recent_story_urls(episode_date, int(s("editorial.avoid_repeat_days", 3)))
        episode, ed_report = run.stage("generate_script", produce_script, mb, geo, rep, episode_date, settings,
                                       secret("GEMINI_API_KEY"), recent, usage_text, args.template,
                                       ledger.meta.get("last_good_text_model"))
        if ed_report.get("text_model"):
            ledger.meta["last_good_text_model"] = ed_report["text_model"]
        settings.set_path("gemini.preferred_model", ledger.meta.get("last_good_text_model"))
        run.report["editorial"] = {k: v for k, v in ed_report.items() if k != "ranking"}
        write_json(out_dir / "episode.json", episode)
        write_json(out_dir / "story_ranking.json", ed_report.get("ranking", {}))
        (out_dir / "script.md").write_text(script_markdown(episode), encoding="utf-8")
        files["script"] = out_dir / "script.md"
        log.info("script: %s, %d words (~%.1f min), flags=%s", episode["generator"], episode["word_count"],
                 episode["estimated_minutes"], episode["flags"])
        if args.script_only:
            run.report["status"] = "script_only"
            return 0

        # 7. narration
        from .audio.narration import narrate
        timeline = run.stage("narration", narrate, episode, out_dir, settings, usage_tts)
        if timeline["voice"]["provider"] != s("tts.provider"):
            episode["flags"].append(f"FALLBACK VOICE: narrated with {timeline['voice']['voice']} "
                                    f"because {s('tts.provider')} failed")
            write_json(out_dir / "episode.json", episode)

        # 8-10. art, subtitles, podcast audio, video
        from .audio.audio_processor import export_mp3, ffprobe_duration
        from .publishing import metadata as md
        from .video.subtitles import build_cues, write_srt, write_vtt
        from .video.thumbnail import make_cover, make_thumbnail

        def art_and_audio():
            files["thumbnail"] = make_thumbnail(episode, out_dir / "thumbnail.jpg")
            files["cover"] = make_cover(out_dir / "cover.jpg")
            cues = build_cues(timeline)
            files["captions"] = out_dir / f"{eid}.srt"
            write_srt(cues, files["captions"])
            write_vtt(cues, out_dir / f"{eid}.vtt")
            files["mp3"] = out_dir / f"{eid}.mp3"
            export_mp3(out_dir / timeline["master"], files["mp3"], {
                "title": md.podcast_title(episode), "artist": s("show.creator"), "album": s("show.name"),
                "date": episode["date"], "genre": "Podcast", "language": "eng",
                "comment": "News and commentary, not investment advice."}, files["cover"])
            files["wav"] = out_dir / timeline["master"]
        run.stage("artwork_subtitles_mp3", art_and_audio)

        if not args.skip_video:
            from .video.renderer import render_video
            files["video"] = out_dir / f"{eid}.mp4"
            vinfo = run.stage("render_video", render_video, episode, timeline, files["wav"], files["video"],
                              settings, args.video_seconds)
            run.report["video"] = vinfo

        # 11. metadata + podcast package + completeness check
        yt_title = md.youtube_title(episode)
        yt_desc = md.youtube_description(episode, timeline, settings)
        tags = md.youtube_tags(episode, settings)
        p_title, p_desc = md.podcast_title(episode), md.podcast_description(episode, timeline, settings)
        write_json(out_dir / "youtube_metadata.json", {"title": yt_title, "description": yt_desc, "tags": tags,
                                                       "category_id": s("publishing.youtube.category_id"),
                                                       "privacy": s("publishing.youtube.privacy")})
        from .publishing.spotify import ManualPublisher, build_package
        pkg = run.stage("podcast_package", build_package, out_dir, episode, files, p_title, p_desc, settings, timeline)

        def completeness():
            need = ["mp3", "wav", "thumbnail", "captions"] + ([] if args.skip_video else ["video"])
            missing = [k for k in need if not files.get(k) or not files[k].exists() or files[k].stat().st_size == 0]
            if missing:
                raise StageError("completeness", f"missing outputs: {missing}")
            a = ffprobe_duration(files["mp3"])
            if abs(a - timeline["duration"]) > 2:
                raise StageError("completeness", f"MP3 duration {a:.1f}s != narration {timeline['duration']:.1f}s")
            if files.get("video") and not args.video_seconds:
                v = ffprobe_duration(files["video"])
                if abs(v - timeline["duration"]) > 2:
                    raise StageError("completeness", f"video duration {v:.1f}s != narration {timeline['duration']:.1f}s")
            return {"audio_seconds": round(a, 1)}
        run.report["completeness"] = run.stage("completeness", completeness)

        story_urls = [x["story"]["url"] for x in episode["segments"] if x.get("type") == "sector" and x.get("story")]
        ledger.update(eid, status="produced", generator=episode["generator"], flags=episode["flags"],
                      voice=timeline["voice"]["voice"], duration=timeline["duration"],
                      words=episode["word_count"], story_urls=story_urls,
                      produced_at=now_utc().isoformat(timespec="seconds"))
        gh_set(produced="true")

        # 12. publishing (only when explicitly enabled, never for flagged/partial episodes).
        # Each destination is independent: a publishing failure never un-produces the episode.
        allow_public = bool(s("publishing.public", False))
        complete_ep = not args.video_seconds and not args.skip_video
        blocked = None
        if episode["flags"] and not args.allow_flagged:
            blocked = "episode is flagged: " + "; ".join(episode["flags"])
        elif not complete_ep:
            blocked = "partial/test render is never published"
        publish_errors = []

        def attempt(dest: str, fn):
            try:
                return run.stage(f"publish_{dest}", fn)
            except StageError as e:
                publish_errors.append(dest)
                log.error("publishing to %s failed: %s", dest, e.message)
                return {"status": "failed", "error": e.message[:500]}

        if not s("publishing.youtube.enabled", False):
            run.report["youtube"] = {"status": "disabled (PUBLISH_YOUTUBE=false)"}
        elif blocked:
            run.report["youtube"] = {"status": "skipped", "reason": blocked}
        elif ledger.youtube_done(eid):
            run.report["youtube"] = {"status": "skipped", "reason": "already uploaded (ledger)",
                                     **(ledger.get(eid).get("youtube") or {})}
        else:
            from .publishing.youtube import publish as yt_publish
            res = attempt("youtube", lambda: yt_publish(episode, files, yt_title, yt_desc, tags,
                                                        settings, allow_public))
            run.report["youtube"] = res
            if res.get("status") != "failed":
                ledger.update(eid, youtube=res)
            ledger.save()

        mode = s("publishing.spotify.mode", "rss")
        if not s("publishing.spotify.enabled", False):
            run.report["spotify"] = {"status": "disabled (PUBLISH_SPOTIFY=false); upload package ready",
                                     "package": str(pkg.relative_to(out_dir))}
        elif mode == "manual":
            run.report["spotify"] = ManualPublisher().publish(pkg, {})
        elif blocked:
            run.report["spotify"] = {"status": "skipped", "reason": blocked}
        else:
            from .publishing import podcast_feed
            run.report["spotify"] = attempt("spotify", lambda: podcast_feed.publish(
                episode, files, p_title, p_desc, timeline, ledger, settings))
            ledger.save()

        published = [d for d in ("youtube", "spotify")
                     if (run.report.get(d) or {}).get("status") in ("uploaded", "already_uploaded", "in_feed")]
        if published:
            before = ledger.get(eid).get("published_to") or []
            ledger.update(eid, status="published", published_to=sorted(set(before) | set(published)))
        if publish_errors:
            run.report["status"] = "produced_publish_failed"
            gh_set(failed_stage="publish_" + "_".join(publish_errors))
            return 1
        run.report["status"] = "ok"
        return 0
    except StageError as e:
        log.error("FAILED at stage '%s': %s", e.stage, e.message)
        run.report["status"] = "failed"
        run.report["failed_stage"] = e.stage
        ledger.update(eid, status="failed", failed_stage=e.stage, error=e.message[:500])
        gh_set(produced="false", failed_stage=e.stage)
        return 1
    finally:
        run.report["quota_usage"] = {"gemini_text": usage_text.as_dict(), "gemini_tts": usage_tts.as_dict()}
        run.save()
        ledger.save()
        print(json.dumps({k: run.report.get(k) for k in ("episode_id", "status", "failed_stage", "youtube")},
                         indent=1))


def cmd_rebuild_feed(_args) -> int:
    """Regenerate docs/podcast/feed.xml from the ledger (e.g. after setting PODCAST_OWNER_EMAIL)."""
    from .publishing.podcast_feed import write_feed
    from .state import Ledger
    settings = load_settings()
    ledger = Ledger(ROOT / settings.get_path("storage.ledger_path", "state/episodes.json"))
    out = write_feed(ledger, settings, None)
    n = sum(1 for r in ledger.data["episodes"].values() if (r.get("podcast") or {}).get("mp3_url"))
    print(f"wrote {out.relative_to(ROOT)} with {n} episode(s); owner email "
          f"{'set' if (os.environ.get('PODCAST_OWNER_EMAIL') or settings.get_path('show.owner_email')) else 'NOT set'}")
    return 0


def cmd_quota(_args) -> int:
    from .quota import estimate
    print(json.dumps(estimate(load_settings()), indent=2))
    return 0


def cmd_check_secrets(_args) -> int:
    for n in SECRET_NAMES:
        print(f"{n:24s} {'set' if secret(n) else 'NOT SET'}")
    return 0


def cmd_youtube_auth_check(_args) -> int:
    from .publishing.youtube import API, HTTP, YouTubeError, access_token
    try:
        tok = access_token()
        r = HTTP.get(f"{API}/channels", params={"part": "snippet", "mine": "true"},
                     headers={"Authorization": f"Bearer {tok}"}, timeout=30)
        items = r.json().get("items") or []
        if r.status_code != 200 or not items:
            print(f"Token works but channel lookup failed: HTTP {r.status_code}")
            return 1
        sn = items[0]["snippet"]
        print(f"OK: refresh token valid; uploads will go to channel '{sn['title']}'")
        print(f"Channel handle: {sn.get('customUrl') or '(none)'}  "
              f"URL: https://www.youtube.com/{sn.get('customUrl') or 'channel/' + items[0]['id']}")
        return 0
    except YouTubeError as e:
        print(f"YouTube auth check FAILED: {e}")
        return 1


def main(argv=None) -> int:
    setup_logging()
    p = argparse.ArgumentParser(prog="market-brief-daily")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="produce one episode")
    r.add_argument("--date", help="episode date (default: today in Europe/Madrid)")
    r.add_argument("--scheduled", action="store_true", help="respect the 08:30 Madrid production window")
    r.add_argument("--force", action="store_true", help="rebuild even if already produced")
    r.add_argument("--template", action="store_true", help="skip Gemini; use the no-AI template script")
    r.add_argument("--script-only", action="store_true", help="stop after the script")
    r.add_argument("--skip-video", action="store_true")
    r.add_argument("--video-seconds", type=float, help="render only the first N seconds (test)")
    r.add_argument("--data-dir", help="read data.json/geo.json from a folder instead of the web")
    r.add_argument("--allow-flagged", action="store_true", help="allow publishing a flagged episode")
    r.set_defaults(fn=cmd_run)
    sub.add_parser("quota").set_defaults(fn=cmd_quota)
    sub.add_parser("rebuild-feed").set_defaults(fn=cmd_rebuild_feed)
    sub.add_parser("check-secrets").set_defaults(fn=cmd_check_secrets)
    sub.add_parser("youtube-auth-check").set_defaults(fn=cmd_youtube_auth_check)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
