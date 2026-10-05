import os
import json
import argparse
import logging
import subprocess
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ================= CONFIGURATION =================
PROCARE_EMAIL = os.getenv("PROCARE_EMAIL")
PROCARE_PASSWORD = os.getenv("PROCARE_PASSWORD")
DOWNLOAD_DIR = os.path.abspath(os.path.expanduser(os.getenv("DOWNLOAD_DIR", "./primrose_photos")))
STATE_FILE = os.getenv("STATE_FILE", "downloaded_ids.json")

# Apple Photos import (macOS only)
PHOTOS_ALBUM = os.getenv("PHOTOS_ALBUM", "Primrose")
IMPORT_TO_PHOTOS = os.getenv("IMPORT_TO_PHOTOS", "true").lower() in ("1", "true", "yes")
PENDING_FILE = os.getenv("PENDING_FILE", "pending_photos_import.json")
IMPORT_BATCH_SIZE = 10
# =================================================

AUTH_URL = "https://online-auth.procareconnect.com/sessions/"
BASE_API_URL = "https://api-school.primrose.procareconnect.com/api/web/parent"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "referer": "https://schools.primrose.procareconnect.com/",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# ---------- state helpers ----------

def _load_json_list(path):
    if os.path.exists(path):
        with open(path, "r") as f:
            return json.load(f)
    return []


def _save_json_list(path, items):
    with open(path, "w") as f:
        json.dump(list(items), f, indent=2)


def load_downloaded_ids():
    return set(_load_json_list(STATE_FILE))


def save_downloaded_ids(downloaded_set):
    _save_json_list(STATE_FILE, sorted(downloaded_set))


def load_pending_imports():
    """Files downloaded but not yet imported into Photos (e.g. a previous import failed)."""
    return _load_json_list(PENDING_FILE)


def save_pending_imports(paths):
    _save_json_list(PENDING_FILE, paths)


# ---------- Procare API ----------

def authenticate():
    if not PROCARE_EMAIL or not PROCARE_PASSWORD:
        raise ValueError("PROCARE_EMAIL and PROCARE_PASSWORD must be set in your .env file or environment.")

    payload = {
        "email": PROCARE_EMAIL,
        "password": PROCARE_PASSWORD,
        "role": "carer",
        "platform": "web",
    }
    resp = requests.post(AUTH_URL, json=payload, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    auth_token = resp.json().get("auth_token")
    if not auth_token:
        raise RuntimeError("Authentication succeeded but no auth_token was returned.")
    logging.info("Successfully authenticated.")
    return auth_token


def get_kids(auth_token):
    auth_headers = {**HEADERS, "authorization": f"Bearer {auth_token}"}
    resp = requests.get(f"{BASE_API_URL}/kids/", headers=auth_headers, timeout=30)
    resp.raise_for_status()
    return resp.json().get("kids", []), auth_headers


def fetch_activities(auth_headers, kid_id, start_date, end_date):
    """Yield every activity across all pages."""
    page, seen = 1, set()
    while True:
        params = {
            "kid_id": kid_id,
            "filters[daily_activity][date_from]": start_date,
            "filters[daily_activity][date_to]": end_date,
            "page": page,
        }
        resp = requests.get(
            f"{BASE_API_URL}/daily_activities/",
            headers=auth_headers,
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("daily_activities", [])

        new = [a for a in batch if a["id"] not in seen]
        if not new:  # empty page, or API ignored `page` and repeated itself
            break
        seen.update(a["id"] for a in new)
        yield from new

        if len(batch) < data.get("per_page", 30):
            break
        page += 1


def extract_photo_urls(activity):
    """Collect every photo URL found on an activity (deduplicated, order preserved)."""
    urls = []
    if activity.get("photo_url"):
        urls.append(activity["photo_url"])
    for u in (activity.get("activiable") or {}).get("urls") or []:
        if isinstance(u, str):
            urls.append(u)
        elif isinstance(u, dict):
            candidate = u.get("url") or u.get("image_url")
            if candidate:
                urls.append(candidate)
    return list(dict.fromkeys(urls))


# ---------- download ----------

def set_file_time_from_activity(file_path, activity):
    """Make the file's timestamp match when the activity happened, so Photos sorts it correctly."""
    try:
        ts = datetime.fromisoformat(activity["activity_time"]).timestamp()
        os.utime(file_path, (ts, ts))
    except (KeyError, ValueError, OSError) as e:
        logging.warning(f"Could not set file time for {file_path}: {e}")


def fetch_and_download_photos(auth_headers, kid_id, start_date, end_date):
    """Download new photos for one kid. Returns the list of newly written file paths."""
    downloaded_ids = load_downloaded_ids()
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    new_paths = []

    for activity in fetch_activities(auth_headers, kid_id, start_date, end_date):
        activity_id = activity["id"]
        activity_date = activity.get("activity_date") or activity["activity_time"][:10]

        for idx, image_url in enumerate(extract_photo_urls(activity)):
            media_id = activity_id if idx == 0 else f"{activity_id}_{idx}"
            if media_id in downloaded_ids:
                continue

            day_folder = os.path.join(DOWNLOAD_DIR, activity_date)
            os.makedirs(day_folder, exist_ok=True)
            file_path = os.path.join(day_folder, f"{media_id}.jpg")

            logging.info(f"Downloading {media_id} -> {day_folder}")
            try:
                img_resp = requests.get(image_url, timeout=60)  # pre-signed URL; no auth header
            except requests.RequestException as e:
                logging.warning(f"{media_id}: download failed: {e}")
                continue

            if img_resp.status_code != 200:
                logging.warning(f"{media_id}: HTTP {img_resp.status_code}")
                continue

            with open(file_path, "wb") as f:
                f.write(img_resp.content)
            set_file_time_from_activity(file_path, activity)

            downloaded_ids.add(media_id)
            save_downloaded_ids(downloaded_ids)  # persist as we go
            new_paths.append(file_path)

    logging.info(f"Downloaded {len(new_paths)} new photo(s) for kid {kid_id}.")
    return new_paths


# ---------- Apple Photos import ----------

def _applescript_escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _import_batch(paths, album):
    files = ", ".join(f'POSIX file "{_applescript_escape(p)}"' for p in paths)
    album_esc = _applescript_escape(album)
    # "skip check duplicates true" avoids a blocking duplicate dialog in a background job;
    # duplicate protection comes from our own state files instead.
    script = f'''
    tell application "Photos"
        with timeout of 3600 seconds
            if not (exists album "{album_esc}") then make new album named "{album_esc}"
            import {{{files}}} into album "{album_esc}" skip check duplicates true
        end timeout
    end tell
    '''
    subprocess.run(
        ["osascript", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=3700,
    )


def import_pending_to_photos(album):
    """Import all pending files into the Photos album. Failed files stay pending for the next run."""
    pending = [p for p in load_pending_imports() if os.path.exists(p)]
    if not pending:
        save_pending_imports([])
        return

    logging.info(f"Importing {len(pending)} photo(s) into Photos album '{album}'...")
    remaining = list(pending)

    for i in range(0, len(pending), IMPORT_BATCH_SIZE):
        batch = pending[i:i + IMPORT_BATCH_SIZE]
        try:
            _import_batch(batch, album)
        except subprocess.CalledProcessError as e:
            logging.error(
                "Photos import failed (is Photos allowed under System Settings > Privacy & Security "
                f"> Automation?): {e.stderr.strip()}"
            )
            break
        except subprocess.TimeoutExpired:
            logging.error("Photos import timed out.")
            break
        remaining = [p for p in remaining if p not in batch]
        save_pending_imports(remaining)  # persist after each successful batch

    save_pending_imports(remaining)
    logging.info(f"Imported {len(pending) - len(remaining)} photo(s); {len(remaining)} still pending.")


# ---------- folder <-> Photos sync (no Procare access needed) ----------

def get_album_filenames(album):
    """Return the set of original filenames already in the Photos album (empty if the album doesn't exist)."""
    album_esc = _applescript_escape(album)
    script = f'''
    tell application "Photos"
        with timeout of 3600 seconds
            if not (exists album "{album_esc}") then return ""
            set fileNames to filename of every media item of album "{album_esc}"
        end timeout
    end tell
    set AppleScript's text item delimiters to linefeed
    return fileNames as text
    '''
    result = subprocess.run(
        ["osascript", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=3700,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def find_local_photos():
    """All downloaded JPEGs under DOWNLOAD_DIR, oldest folder first."""
    paths = []
    for root, _dirs, files in os.walk(DOWNLOAD_DIR):
        for name in files:
            if name.lower().endswith((".jpg", ".jpeg")):
                paths.append(os.path.join(root, name))
    return sorted(paths)


def sync_folder_with_photos(album, dry_run=False):
    """Import every photo in the download folder that isn't already in the Photos album."""
    local = find_local_photos()
    logging.info(f"Found {len(local)} photo(s) in {DOWNLOAD_DIR}.")

    try:
        in_album = get_album_filenames(album)
    except subprocess.CalledProcessError as e:
        logging.error(
            "Could not read the Photos album (check System Settings > Privacy & Security > Automation): "
            f"{e.stderr.strip()}"
        )
        return
    except subprocess.TimeoutExpired:
        logging.error("Timed out reading the Photos album.")
        return

    missing = [p for p in local if os.path.basename(p) not in in_album]
    logging.info(f"{len(in_album)} photo(s) already in album '{album}'; {len(missing)} to import.")

    if dry_run:
        for p in missing[:10]:
            logging.info(f"  would import: {p}")
        if len(missing) > 10:
            logging.info(f"  ... and {len(missing) - 10} more")
        return

    save_pending_imports(missing)  # replaces any stale queue; progress is saved per batch
    if missing:
        import_pending_to_photos(album)
    logging.info("Sync complete.")


# ---------- main ----------

def main(start_date, end_date, use_photos, album):
    token = authenticate()
    kids, auth_headers = get_kids(token)

    if not kids:
        logging.info("No kids associated with this account.")
        return

    all_new = []
    for kid in kids:
        kid_id = kid.get("id")
        kid_name = kid.get("first_name", "Kid")
        logging.info(f"Fetching photos for {kid_name} (ID: {kid_id})...")
        all_new.extend(fetch_and_download_photos(auth_headers, kid_id, start_date, end_date))

    if use_photos:
        # Queue new files first, so they survive if the import step fails.
        save_pending_imports(load_pending_imports() + all_new)
        import_pending_to_photos(album)

    logging.info(f"Finished. {len(all_new)} new photo(s) downloaded this run.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download photos from Procare and import them into Apple Photos.")
    parser.add_argument(
        "start_date", type=str, nargs="?",
        default=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
        help="Start date (YYYY-MM-DD). Default: yesterday",
    )
    parser.add_argument(
        "end_date", type=str, nargs="?",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="End date (YYYY-MM-DD). Default: today",
    )
    parser.add_argument("--album", default=PHOTOS_ALBUM, help=f"Photos album name (default: {PHOTOS_ALBUM})")
    parser.add_argument("--no-photos", action="store_true", help="Download only; skip the Apple Photos import")
    parser.add_argument(
        "--sync-only", action="store_true",
        help="Skip Procare entirely; import any photos in the download folder that aren't in the Photos album",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="With --sync-only: list what would be imported without importing anything",
    )
    args = parser.parse_args()

    if args.sync_only and args.no_photos:
        parser.error("--sync-only and --no-photos can't be used together")
    if args.dry_run and not args.sync_only:
        parser.error("--dry-run only works with --sync-only")

    if args.sync_only:
        sync_folder_with_photos(args.album, dry_run=args.dry_run)
    else:
        main(args.start_date, args.end_date, use_photos=IMPORT_TO_PHOTOS and not args.no_photos, album=args.album)