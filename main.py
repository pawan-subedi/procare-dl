import os
import json
import requests
from datetime import datetime

# ================= CONFIGURATION =================
PROCARE_EMAIL = "your_email@example.com"
PROCARE_PASSWORD = "your_password"
DOWNLOAD_DIR = "./primrose_photos"
STATE_FILE = "downloaded_ids.json"
# =================================================

AUTH_URL = "https://online-auth.procareconnect.com/sessions/"
BASE_API_URL = "https://api-school.primrose.procareconnect.com/api/web/parent"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "referrer": "https://schools.primrose.procareconnect.com/",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

def load_downloaded_ids():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_downloaded_ids(downloaded_set):
    with open(STATE_FILE, "w") as f:
        json.dump(list(downloaded_set), f, indent=2)

def authenticate():
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
    print("Successfully authenticated.")
    return auth_token

def get_kids(auth_token):
    auth_headers = {**HEADERS, "authorization": f"Bearer {auth_token}"}
    resp = requests.get(f"{BASE_API_URL}/kids/", headers=auth_headers)
    resp.raise_for_status()
    kids = resp.json().get("kids", [])
    return kids, auth_headers

def fetch_and_download_photos(auth_headers, kid_id):
    today = datetime.now().strftime("%Y-%m-%d")
    url = f"{BASE_API_URL}/daily_activities/?kid_id={kid_id}&filters[daily_activity][date_to]={today}&page=1"
    
    resp = requests.get(url, headers=auth_headers)
    resp.raise_for_status()
    activities = resp.json().get("daily_activities", [])

    downloaded_ids = load_downloaded_ids()
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    new_downloads_count = 0

    for activity in activities:
        activity_id = activity.get("id")
        # Check for attached media/photos in the activity object
        media_list = activity.get("media", []) or activity.get("photos", [])
        
        for idx, media in enumerate(media_list):
            image_url = media.get("url") or media.get("image_url")
            media_id = media.get("id", f"{activity_id}_{idx}")

            if not image_url or media_id in downloaded_ids:
                continue

            # Determine file path based on creation/activity date
            activity_date = activity.get("created_at", today)[:10]
            day_folder = os.path.join(DOWNLOAD_DIR, activity_date)
            os.makedirs(day_folder, exist_ok=True)

            file_name = f"{media_id}.jpg"
            file_path = os.path.join(day_folder, file_name)

            print(f"Downloading new photo: {file_name} -> {day_folder}")
            img_resp = requests.get(image_url)
            if img_resp.status_code == 200:
                with open(file_path, "wb") as img_file:
                    img_file.write(img_resp.content)
                downloaded_ids.add(media_id)
                new_downloads_count += 1

    save_downloaded_ids(downloaded_ids)
    print(f"Finished. Downloaded {new_downloads_count} new photo(s).")

def main():
    token = authenticate()
    kids, auth_headers = get_kids(token)
    
    if not kids:
        print("No kids associated with this account.")
        return

    for kid in kids:
        kid_id = kid.get("id")
        kid_name = kid.get("first_name", "Kid")
        print(f"Fetching photos for {kid_name} (ID: {kid_id})...")
        fetch_and_download_photos(auth_headers, kid_id)

if __name__ == "__main__":
    main()