# InstaReel

Small Python pipeline: Grok writes a short script and visual prompt, Grok Imagine creates a vertical clip, ElevenLabs makes narration, FFmpeg combines them, and Instagram's official API can publish the Reel.

## Setup

1. Install Python 3.10+ and FFmpeg, then make sure `ffmpeg` is on PATH.
2. Copy `.env.example` to `.env` and fill in the xAI key, ElevenLabs key, and ElevenLabs voice ID. The same xAI key is used for Grok text and video.
3. Run `python pipeline.py`.

No key is stored in the code or committed to Git. The template is set to publish automatically once all Instagram credentials are filled in; set `AUTO_PUBLISH=false` to run in draft-only mode.

## Instagram publishing

Automatic publishing needs an Instagram **professional** account connected to a Meta app, an access token with `instagram_content_publish`, and the account's Instagram user ID. Set `IG_USER_ID`, `IG_ACCESS_TOKEN`, and the current Graph API version in `.env`. Use `AUTO_PUBLISH=false` for a draft-only run; otherwise each scheduled run publishes automatically.

To run daily on Windows, create a Task Scheduler task with the action `python.exe <full path to this folder>\pipeline.py` and set its working directory to this folder. Keep the computer awake and connected at the scheduled time, or run it on an always-on host.

Publishing uses Meta's resumable upload flow, so the finished video does not need to be hosted at a public URL. Each run generates and publishes one fresh Reel; the script and caption are also saved locally.

## Configuration

Change `GENRE` in `.env` to steer the daily topic. Adjust the model IDs or output folder there too. Set `AUTO_PUBLISH=false` to generate drafts without posting.
