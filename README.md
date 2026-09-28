# Procare Photo Downloader

Downloads photos from your child's Procare (Primrose) daily activity feed and saves them into folders by date. Already-downloaded photos are tracked in a state file, so re-running is safe and never creates duplicates.

```
primrose_photos/
├── 2026-09-25/
│   ├── 6444ac4a-3f7c-43f9-bb54-579c7f21703c.jpg
│   └── ...
└── 2026-09-28/
    └── ...
```

## Requirements

- macOS (the scheduling section uses launchd)
- Python 3.9+
- A Procare parent account (email + password)

## Setup

### 1. Install dependencies

```bash
cd ~/projects/primrose-photos     # wherever main.py lives
python3 -m venv venv
source venv/bin/activate
pip install requests python-dotenv
```

### 2. Create your `.env` file

Create a file named `.env` next to `main.py`:

```
PROCARE_EMAIL=you@example.com
PROCARE_PASSWORD=your-password

# Optional (defaults shown)
DOWNLOAD_DIR=./primrose_photos
STATE_FILE=downloaded_ids.json
```

Lock it down, since it contains your password:

```bash
chmod 600 .env
```

If you use git, add these to `.gitignore` so credentials and photos never get committed:

```
.env
venv/
primrose_photos/
downloaded_ids.json
run.log
```

## Usage

Run manually with no arguments to fetch yesterday through today:

```bash
source venv/bin/activate
python main.py
```

Or pass a date range (`YYYY-MM-DD`) to backfill:

```bash
python main.py 2026-08-01 2026-09-28
```

Notes:

- Photo URLs from Procare are pre-signed and expire, so the script downloads them immediately after fetching.
- To re-download everything, delete `downloaded_ids.json`.

## Scheduling: run every day at 7:00 PM (macOS)

Use a launchd LaunchAgent rather than cron. If your Mac is asleep at 7pm, launchd runs the missed job on wake; cron skips it.

### 1. Find your absolute paths

launchd doesn't expand `~` or use your shell environment, so use full paths.

```bash
cd ~/projects/primrose-photos
pwd                           # project directory
source venv/bin/activate
which python                  # venv python interpreter
```

### 2. Create the plist

Save the following as `~/Library/LaunchAgents/com.user.primrose-photos.plist`, replacing every `/Users/YOU/projects/primrose-photos` with your real project path from step 1:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.primrose-photos</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/YOU/projects/primrose-photos/venv/bin/python</string>
        <string>/Users/YOU/projects/primrose-photos/main.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/YOU/projects/primrose-photos</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>19</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>/Users/YOU/projects/primrose-photos/run.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOU/projects/primrose-photos/run.log</string>
</dict>
</plist>
```

What each key does:

| Key | Purpose |
|---|---|
| `Label` | Unique job name. Must match the plist filename (without `.plist`). |
| `ProgramArguments` | The venv Python interpreter followed by the script. Add date args here if you want a custom range. |
| `WorkingDirectory` | Required so `.env`, `downloaded_ids.json`, and `./primrose_photos` resolve to your project folder. |
| `StartCalendarInterval` | Runs daily at 19:00 (7:00 PM). |
| `StandardOutPath` / `StandardErrorPath` | Where output and errors are logged. |

Validate the file before loading it:

```bash
plutil -lint ~/Library/LaunchAgents/com.user.primrose-photos.plist
```

### 3. Load the job

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.primrose-photos.plist
```

### 4. Test it now instead of waiting for 7pm

```bash
launchctl kickstart -k gui/$(id -u)/com.user.primrose-photos
tail -f ~/projects/primrose-photos/run.log
```

### 5. Check status

```bash
launchctl print gui/$(id -u)/com.user.primrose-photos
```

Look at `last exit code` (0 means success) and `state`.

### Changing or removing the schedule

After editing the plist, unload and reload it:

```bash
launchctl bootout gui/$(id -u)/com.user.primrose-photos
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.primrose-photos.plist
```

To remove it permanently:

```bash
launchctl bootout gui/$(id -u)/com.user.primrose-photos
rm ~/Library/LaunchAgents/com.user.primrose-photos.plist
```

### Sleep and power behavior

- **Asleep at 7:00 PM:** the job runs when the Mac wakes.
- **Powered off at 7:00 PM:** the job does not run until the next scheduled time. The default "yesterday to today" window means the next run picks up anything missed.
- **Wake the Mac just before the job (optional):**

  ```bash
  sudo pmset repeat wakeorpoweron MTWRFSU 18:59:00
  ```

  Cancel it with `sudo pmset repeat cancel`.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| Nothing in `run.log`, job never runs | Run `plutil -lint` on the plist. Confirm the paths are absolute and the `Label` matches the filename. |
| `PROCARE_EMAIL and PROCARE_PASSWORD must be set` | `WorkingDirectory` is wrong, so `.env` isn't found. Use the full project path. |
| `Operation not permitted` or files silently missing | The project or download folder is in `~/Documents`, `~/Desktop`, or `~/Downloads`. macOS privacy controls block background jobs there. Move the project (for example to `~/projects`) or grant your Python binary Full Disk Access in System Settings → Privacy & Security. |
| `ModuleNotFoundError: requests` | The plist points at system Python instead of the venv Python. Use the `which python` path from inside the activated venv. |
| Connection errors right after waking | Wi-Fi wasn't up yet. The next run backfills automatically. |
| `401` or `403` from Procare | Wrong credentials, or the account requires an extra login step. Verify by logging in on the Procare website. |
| Photo download returns 403 | The signed URL expired. Re-run the script so it fetches fresh URLs. |
| `run.log` is huge and noisy | Change `logging.basicConfig(level=logging.DEBUG, ...)` to `logging.INFO` in `main.py`. |

## Privacy

This tool stores photos of a child and your account password on your machine. Keep `.env` at `chmod 600`, keep the photo folder out of shared or synced-public locations, and never commit either to version control.