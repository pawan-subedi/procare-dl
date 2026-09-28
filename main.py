import os
import json
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
import argparse
import logging

# Load environment variables from .env file
load_dotenv()

# ================= CONFIGURATION =================
PROCARE_EMAIL = os.getenv("PROCARE_EMAIL")
PROCARE_PASSWORD = os.getenv("PROCARE_PASSWORD")
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "./primrose_photos")
STATE_FILE = os.getenv("STATE_FILE", "downloaded_ids.json")
# =================================================

AUTH_URL = "https://online-auth.procareconnect.com/sessions/"
BASE_API_URL = "https://api-school.primrose.procareconnect.com/api/web/parent"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "referrer": "https://schools.primrose.procareconnect.com/",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

# Set up logging configuration
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

def load_downloaded_ids():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_downloaded_ids(downloaded_set):
    with open(STATE_FILE, "w") as f:
        json.dump(list(downloaded_set), f, indent=2)

def authenticate():
    if not PROCARE_EMAIL or not PROCARE_PASSWORD:
        raise ValueError("PROCARE_EMAIL and PROCARE_PASSWORD must be set in your .env file or environment.")

    payload = {
        "email": PROCARE_EMAIL,
        "password": PROCARE_PASSWORD,
        "role": "carer",
        "platform": "web"
    }
    resp = requests.post(AUTH_URL, json=payload, headers=HEADERS)
    resp.raise_for_status()
    data = resp.json()
    auth_token = data.get("auth_token")
    logging.info("Successfully authenticated.")
    return auth_token

def get_kids(auth_token):
    auth_headers = {**HEADERS, "authorization": f"Bearer {auth_token}"}
    resp = requests.get(f"{BASE_API_URL}/kids/", headers=auth_headers)
    resp.raise_for_status()
    kids = resp.json().get("kids", [])
    return kids, auth_headers

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
        resp = requests.get(f"{BASE_API_URL}/daily_activities/",
                            headers=auth_headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("daily_activities", [])

        new = [a for a in batch if a["id"] not in seen]
        if not new:          # empty page, or API ignored `page` and repeated itself
            break
        seen.update(a["id"] for a in new)
        yield from new

        if len(batch) < data.get("per_page", 30):
            break
        page += 1


def extract_photo_urls(activity):
    urls = []
    if activity.get("photo_url"):
        urls.append(activity["photo_url"])
    for u in (activity.get("activiable") or {}).get("urls") or []:
        if isinstance(u, str):
            urls.append(u)
        elif isinstance(u, dict) and (u.get("url") or u.get("image_url")):
            urls.append(u.get("url") or u.get("image_url"))
    return urls


def fetch_and_download_photos(auth_headers, kid_id, start_date, end_date):
    downloaded_ids = load_downloaded_ids()
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    new_count = 0

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
            img_resp = requests.get(image_url, timeout=60)  # pre-signed; no auth header
            if img_resp.status_code == 200:
                with open(file_path, "wb") as f:
                    f.write(img_resp.content)
                downloaded_ids.add(media_id)
                new_count += 1
                save_downloaded_ids(downloaded_ids)  # persist as you go
            else:
                logging.warning(f"{media_id}: HTTP {img_resp.status_code}")

    logging.info(f"Finished. Downloaded {new_count} new photo(s).")
def main(start_date, end_date):
    token = authenticate()
    kids, auth_headers = get_kids(token)
    
    if not kids:
        logging.info("No kids associated with this account.")
        return

    for kid in kids:
        kid_id = kid.get("id")
        kid_name = kid.get("first_name", "Kid")
        logging.info(f"Fetching photos for {kid_name} (ID: {kid_id})...")
        fetch_and_download_photos(auth_headers, kid_id, start_date, end_date)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download photos from Procare API.")
    parser.add_argument("start_date", type=str, nargs='?', default=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"), help="Start date (YYYY-MM-DD)")
    parser.add_argument("end_date", type=str, nargs='?', default=datetime.now().strftime("%Y-%m-%d"), help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    main(args.start_date, args.end_date)