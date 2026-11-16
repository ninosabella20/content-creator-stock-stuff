import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from PIL import Image, ImageDraw
from src.video import graphics as g

W, H = 2400, 1500
BG = (255, 255, 255); INK = (20, 28, 45); MUT = (90, 100, 120)
COL = {"ext": (232, 240, 254), "gh": (236, 245, 236), "ggl": (254, 243, 224), "yt": (253, 232, 232), "you": (240, 236, 250)}
EDGE = {"ext": (66, 133, 244), "gh": (52, 168, 83), "ggl": (234, 140, 0), "yt": (219, 68, 55), "you": (120, 90, 200)}

def box(d, xy, title, lines, kind):
    x0, y0, x1, y1 = xy
    d.rounded_rectangle(xy, 22, fill=COL[kind], outline=EDGE[kind], width=4)
    size = 34
    while size > 22 and d.textlength(title, font=g.font("Bold", size)) > (x1 - x0) - 40:
        size -= 2
    d.text(((x0 + x1) / 2, y0 + 22), title, font=g.font("Bold", size), fill=INK, anchor="mt")
    y = y0 + 76
    for ln in lines:
        d.text(((x0 + x1) / 2, y), ln, font=g.font("Regular", 25), fill=MUT, anchor="mt")
        y += 36

def arrow(d, p0, p1, label="", color=INK, lab_off=(0, -30)):
    import math
    d.line([p0, p1], fill=color, width=5)
    a = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
    for s in (2.6, -2.6):
        d.line([p1, (p1[0] + 26 * math.cos(a + s), p1[1] + 26 * math.sin(a + s))], fill=color, width=5)
    if label:
        mx, my = (p0[0] + p1[0]) / 2 + lab_off[0], (p0[1] + p1[1]) / 2 + lab_off[1]
        f = g.font("SemiBold", 24)
        tw = d.textlength(label, font=f)
        d.rectangle((mx - tw / 2 - 8, my - 16, mx + tw / 2 + 8, my + 18), fill=BG)
        d.text((mx, my), label, font=f, fill=color, anchor="mm")

def header(d, title, sub):
    d.text((W / 2, 50), title, font=g.font("Bold", 52), fill=INK, anchor="mt")
    d.text((W / 2, 120), sub, font=g.font("Regular", 28), fill=MUT, anchor="mt")

# ---------------- architecture ----------------
img = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(img)
header(d, "Market Brief Podcast – System Architecture",
       "Single-user automation: uploads only to the owner's own YouTube channel. No end users, no user data.")
box(d, (60, 230, 620, 520), "Data sources (public)", ["Market Brief JSON (GitHub)", "Yahoo Finance closing prices", "read-only, no YouTube data"], "ext")
box(d, (60, 620, 620, 900), "Scheduler", ["cron-job.org, 09:00 Madrid Mon-Sat", "calls GitHub workflow_dispatch", "(GitHub cron as backup)"], "ext")
box(d, (820, 300, 1580, 900), "GitHub Actions (owner's repo)", [
    "content-creator-stock-stuff", "1. fetch & validate data", "2. write script (Gemini API)",
    "3. narrate (Gemini TTS, AI voice)", "4. render 1080p video (FFmpeg)", "5. subtitles, thumbnail",
    "6. upload via YouTube Data API", "7. podcast RSS (GitHub Pages)", "", "Secrets: OAuth client + refresh",
    "token (encrypted GitHub Secrets)"], "gh")
box(d, (1780, 230, 2340, 520), "Google Gemini API", ["script generation", "text-to-speech narration"], "ggl")
box(d, (1780, 620, 2340, 1000), "YouTube Data API v3", ["OAuth 2.0 (owner's consent)", "videos.insert", "thumbnails.set, captions.insert", "channels.list, playlistItems.list", "(own channel only)"], "yt")
box(d, (1780, 1110, 2340, 1400), "Owner's channel", ["\"Active Thinker\"", "@activethinker-t5v", "viewers watch on YouTube"], "you")
box(d, (820, 1010, 1580, 1300), "Spotify (podcast)", ["reads public RSS feed", "(no YouTube API involved)"], "ext")
arrow(d, (620, 375), (820, 420), "HTTPS GET", EDGE["ext"])
arrow(d, (620, 760), (820, 700), "trigger", EDGE["ext"])
arrow(d, (1580, 420), (1780, 375), "API key", EDGE["ggl"])
arrow(d, (1580, 780), (1780, 800), "OAuth 2.0 access token", EDGE["yt"])
arrow(d, (2060, 1000), (2060, 1110), "", EDGE["yt"])
arrow(d, (1200, 900), (1200, 1010), "RSS feed", EDGE["gh"], (110, 0))
d.text((W / 2, H - 60), "Data stored: none from YouTube. Only the owner's OAuth refresh token, as an encrypted GitHub Actions secret.",
       font=g.font("Medium", 26), fill=MUT, anchor="mm")
img.save(str(__import__("pathlib").Path(__file__).resolve().parent.parent / "docs/audit/architecture.png"))

# ---------------- user / OAuth flow ----------------
H2 = 1700
img = Image.new("RGB", (W, H2), BG); d = ImageDraw.Draw(img)
header(d, "Market Brief Podcast – User & OAuth Flow",
       "The only user is the channel owner. Viewers never sign in to or interact with the API client.")
d.text((300, 200), "ONE-TIME SETUP (owner)", font=g.font("Bold", 32), fill=EDGE["you"], anchor="mt")
d.text((1500, 200), "EVERY TRADING DAY (automatic, no user interaction)", font=g.font("Bold", 32), fill=EDGE["gh"], anchor="mt")
steps1 = [("1. Owner runs authorisation script", ["scripts/get_youtube_refresh_token.py", "(OAuth 'installed app' flow, PKCE)"]),
          ("2. Google consent screen", ["\"Market Brief Podcast\" requests", "youtube.upload, youtube.force-ssl"]),
          ("3. Owner signs in & allows", ["selects own channel", "\"Active Thinker\""]),
          ("4. Refresh token saved", ["as encrypted GitHub Actions secret", "(never in code or logs)"])]
y = 260
for i, (t, ls) in enumerate(steps1):
    box(d, (60, y, 540, y + 250), t, ls, "you")
    if i < 3:
        arrow(d, (300, y + 250), (300, y + 330))
    y += 330
steps2 = [("A. Scheduled workflow starts", ["09:00 Madrid, Mon-Sat"], "gh"),
          ("B. Build episode", ["script, AI voice, video", "(Gemini API, FFmpeg)"], "gh"),
          ("C. Get access token", ["refresh token -> Google OAuth", "token endpoint"], "ggl"),
          ("D. Duplicate check", ["channels.list + playlistItems.list", "(owner's own uploads)"], "yt"),
          ("E. Upload", ["videos.insert (resumable)", "thumbnails.set, captions.insert"], "yt"),
          ("F. Published on own channel", ["visibility per owner setting", "AI narration disclosed"], "you")]
xs = [800, 1340, 1880]
pos = []
for i, (t, ls, k) in enumerate(steps2):
    col, row = i % 3, i // 3
    x0 = xs[col]; y0 = 300 + row * 520
    box(d, (x0, y0, x0 + 480, y0 + 260), t, ls, k)
    pos.append((x0, y0))
for i in range(5):
    (ax, ay), (bx, by) = pos[i], pos[i + 1]
    if i == 2:   # C (end of row 1) -> D (start of row 2): down, left, down
        cx = ax + 240
        midy = ay + 260 + 120
        d.line([(cx, ay + 260), (cx, midy)], fill=INK, width=5)
        d.line([(cx, midy), (bx + 240, midy)], fill=INK, width=5)
        arrow(d, (bx + 240, midy), (bx + 240, by), "")
    else:
        if by == ay:
            arrow(d, (ax + 480, ay + 130), (bx, by + 130)) if bx > ax else arrow(d, (ax, ay + 130), (bx + 480, by + 130))
d.line([(660, 260), (660, H2 - 180)], fill=(210, 214, 222), width=3)
box(d, (800, 1380, 2360, 1600), "Owner controls", ["revoke anytime at myaccount.google.com/permissions  ·  publishing on/off via repository variables",
                                                    "no other users, no user data collected, no YouTube data stored or shown to anyone"], "ext")
img.save(str(__import__("pathlib").Path(__file__).resolve().parent.parent / "docs/audit/user_flow.png"))
print("ok")
