#!/usr/bin/env python3
import json
import os
import urllib.request
import urllib.error
import subprocess
import re
import xml.etree.ElementTree as ET
import time
import base64
import hashlib
import hmac
from datetime import datetime, timezone, timedelta
from google import genai
from google.genai import types
from google.genai.errors import APIError

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

def _fallback_load_env(filepath):
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip().strip("'").strip('"')
                    if key and key not in os.environ:
                        os.environ[key] = val
        except Exception as e:
            pass

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    load_dotenv()
except ImportError:
    _fallback_load_env(os.path.join(PROJECT_ROOT, ".env"))
    _fallback_load_env(os.path.join(SCRIPT_DIR, ".env"))

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# Target Configuration
SUBREDDIT_JSON_URL = os.environ.get("SUBREDDIT_JSON_URL", "https://www.reddit.com/r/chromecast/new.json?limit=100")
SUBREDDIT_RSS_URL = os.environ.get("SUBREDDIT_RSS_URL", "https://www.reddit.com/r/chromecast/new/.rss")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
SHEET_RANGE = os.environ.get("SHEET_RANGE", "Sheet1!A:L")
USER_AGENT = os.environ.get("USER_AGENT", "macOS:r-chromecast-monitor:v1.0.0 (by /u/pinchieh)")

_sa_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.environ.get("SERVICE_ACCOUNT_FILE", "")
if _sa_file:
    if not os.path.isabs(_sa_file):
        SERVICE_ACCOUNT_FILE = os.path.join(PROJECT_ROOT, _sa_file)
    else:
        SERVICE_ACCOUNT_FILE = _sa_file
else:
    SERVICE_ACCOUNT_FILE = os.path.join(SCRIPT_DIR, "reddit-chromecast-tracker-credentials.json")

def strip_html_tags(html_str):
    if not html_str:
        return ""
    clean = re.compile('<.*?>')
    return re.sub(clean, '', html_str)

def fetch_json_posts():
    print(f"Attempting to fetch posts from Reddit JSON API: {SUBREDDIT_JSON_URL}")
    req = urllib.request.Request(
        SUBREDDIT_JSON_URL,
        headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                posts = data.get("data", {}).get("children", [])
                print(f"Successfully fetched {len(posts)} posts from JSON API.")
                return posts
    except Exception as e:
        print(f"JSON fetch failed or rate limited: {e}")
    return None

def fetch_rss_posts():
    print(f"Attempting to fetch posts from Reddit RSS/Atom feed: {SUBREDDIT_RSS_URL}")
    req = urllib.request.Request(
        SUBREDDIT_RSS_URL,
        headers={"User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                xml_data = response.read()
                root = ET.fromstring(xml_data)
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                entries = root.findall('atom:entry', ns)
                print(f"Successfully fetched {len(entries)} posts from RSS feed.")
                return entries, ns
    except Exception as e:
        print(f"RSS fetch failed: {e}")
    return None, None

def get_auth_token():
    return get_service_account_token_pure_python()

def analyze_post_with_gemini(title, body, token=None):
    global client
    if client is None:
        key = os.environ.get("GEMINI_API_KEY", "")
        if key:
            client = genai.Client(api_key=key)
        else:
            print("[Error] Gemini client is not initialized (missing GEMINI_API_KEY).")
            return {
                "engineering_component": "General/Other",
                "device": "Unknown/Generic",
                "symptom": "Connectivity > Unknown",
                "severity": "Minor",
                "summary_en": "Unknown (Fallback)",
                "next_step": "Monitor trend scale",
                "remarks": "Missing GEMINI_API_KEY"
            }
    system_instruction = (
        "You are an expert technical support engineer for Google Cast hardware and SDK ecosystems. Analyze the following Reddit post title and body, and return a JSON object with these exact keys: \"engineering_component\", \"device\", \"symptom\", \"severity\", \"summary_en\", \"next_step\".\n\n"
        "Rules for fields:\n"
        "- \"engineering_component\": Determine which sub-team or architectural layer owns the issue. Choose exactly ONE from: [\"SDK > Android Sender\", \"SDK > iOS Sender\", \"SDK > Receiver\", \"Platform > Android TV / Google TV\", \"Platform > Cast Built-in (OEM or Home App)\", \"Feature > Mirroring\", \"External App Issue\", \"General/Other\"]\n"
        "- \"device\": Identify both sender and receiver devices mentioned. Standard format: \"[Sender OS/Device] -> [Receiver Hardware]\" (e.g., \"iOS (iPhone 17) -> CCwGTV 4K\", \"Android -> Streamer 4K\", \"Unknown -> Nest Mini\")\n"
        "- \"symptom\": Group by symptom category following gUP user-pain-point taxonomy guidelines (e.g., \"Connectivity > Discovery Failure\", \"Connectivity > Frequent Disconnects\", \"Media Playback > Audio Sync / Cutout\", \"App Setup > Setup Stalled\", \"Remote Control > Input Unresponsive\", \"General / Other\")\n"
        "- \"severity\": Choose exactly ONE from: [\"P1\", \"P2\", \"P3\",\"P4\"]\n"
        "- \"summary_en\": One concise sentence in English summarizing the user's technical pain point.\n"
        "- \"next_step\": A professional actionable engineering next step."
    )
    
    prompt_text = f"Title: {title}\nBody: {body}"
    
    def clean_and_parse(text):
        clean_text = text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
        clean_text = clean_text.strip()
        
        try:
            # Layer 1: Standard parsing
            parsed = json.loads(clean_text)
        except json.JSONDecodeError:
            try:
                # Layer 2: Escaping fallback for common control character issues
                escaped_text = clean_text.replace('\n', '\\n').replace('\r', '\\r')
                parsed = json.loads(escaped_text)
            except json.JSONDecodeError as jde:
                print(f"[Warning] JSON parser fallback failed: {jde}. Activating Regex extractor.")
                # Layer 3: Regex fallback to extract fields if JSON is completely malformed
                def extract_field(field_name, default_val):
                    # Regex pattern to capture string content while respecting escaped quotes
                    match = re.search(rf'"{field_name}"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', clean_text)
                    if match:
                        return match.group(1)
                    return default_val
                
                parsed = {
                    "engineering_component": extract_field("engineering_component", "General/Other"),
                    "device": extract_field("device", "Unknown/Generic"),
                    "symptom": extract_field("symptom", "Connectivity > Unknown"),
                    "severity": extract_field("severity", "Minor"),
                    "summary_en": extract_field("summary_en", f"Unknown (Title: {title[:40]})"),
                    "next_step": extract_field("next_step", "Monitor trend scale")
                }

        return {
            "engineering_component": parsed.get("engineering_component", "General/Other"),
            "device": parsed.get("device", "Unknown/Generic"),
            "symptom": parsed.get("symptom", "Connectivity > Unknown"),
            "severity": parsed.get("severity", "Minor"),
            "summary_en": parsed.get("summary_en", f"Unknown (Title: {title[:40]})"),
            "next_step": parsed.get("next_step", "Monitor trend scale"),
            "remarks": ""
        }

    max_retries = 3
    backoff_sec = 12
    
    for attempt in range(max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-3.5-flash",
                contents=prompt_text,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    temperature=0.2
                )
            )
            if response and response.text:
                return clean_and_parse(response.text)
                
        except APIError as e:
            # Detect 429 or Resource Exhausted
            is_rate_limit = (e.code == 429 or "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e))
            
            if is_rate_limit and attempt < max_retries:
                print(f"[Warning] Gemini 429 rate limit hit. Retrying in {backoff_sec}s (Attempt {attempt + 1}/{max_retries})...")
                time.sleep(backoff_sec)
                backoff_sec *= 2  # Exponential backoff (12s -> 24s -> 48s)
                continue
            else:
                print(f"[Error] Gemini API invocation failed (Max retries reached or non-429 error): {e}")
                break
        except Exception as e:
            print(f"[Error] Unexpected error during Gemini call: {e}")
            break

    print(f"Gemini AI classification fallback triggered for post: '{title[:30]}...'")
    return {
        "engineering_component": "General/Other",
        "device": "Unknown/Generic",
        "symptom": "Connectivity > Unknown",
        "severity": "Minor",
        "summary_en": "Unknown (Fallback)",
        "next_step": "Monitor trend scale",
        "remarks": "Exceeded Gemini API rate limit (Structured fallback applied)"
    }

def process_json_posts(posts, cutoff_time, token=None):
    rows = []
    for post in posts:
        post_data = post.get("data", {})
        created_utc_val = post_data.get("created_utc")
        if not created_utc_val:
            continue
            
        post_time = datetime.fromtimestamp(created_utc_val, tz=timezone.utc)
        if post_time >= cutoff_time:
            created_date = post_time.strftime("%Y-%m-%d")
            title = post_data.get("title", "")
            body = post_data.get("selftext", "")
            permalink = post_data.get("permalink", "")
            full_url = f"https://www.reddit.com{permalink}"
            
            clean_title = title.strip()
            clean_body = body.strip()
            if not clean_title or clean_title.lower() in ["[deleted]", "[removed]"] or (len(clean_title) < 5 and len(clean_body) < 5):
                print(f"Skipping empty/spam post: '{clean_title}'")
                continue
                
            try:
                ai_data = analyze_post_with_gemini(title, body, token)
                # Safeguard cooldown to guarantee < 10 RPM (Free Tier Limit)
                time.sleep(8.0)
                updated_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                rows.append([created_date, ai_data["engineering_component"], ai_data["device"], ai_data["symptom"], ai_data["severity"], title, body, ai_data["summary_en"], ai_data["next_step"], full_url, updated_time, ai_data["remarks"]])
            except Exception as e:
                print(f"[Error] Row processing fallback triggered: {e}")
                rows.append([
                    created_date,
                    "General/Other",
                    "Unknown/Generic",
                    "Connectivity > Unknown",
                    "Minor",
                    title,
                    body,
                    "Unknown (Fallback)",
                    "Monitor trend scale",
                    full_url,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "Exceeded Gemini API rate limit (Structured fallback applied)"
                ])
    return rows

def process_rss_posts(entries, ns, cutoff_time, token=None):
    rows = []
    for entry in entries:
        updated_str = entry.find('atom:updated', ns).text
        post_time = datetime.fromisoformat(updated_str)
        
        if post_time >= cutoff_time:
            created_date = post_time.strftime("%Y-%m-%d")
            title = entry.find('atom:title', ns).text
            
            link_elem = entry.find('atom:link', ns)
            full_url = link_elem.attrib.get('href') if link_elem is not None else ""
            
            content_elem = entry.find('atom:content', ns)
            raw_content = content_elem.text if content_elem is not None else ""
            body = strip_html_tags(raw_content)
            
            clean_title = title.strip() if title else ""
            clean_body = body.strip() if body else ""
            if not clean_title or clean_title.lower() in ["[deleted]", "[removed]"] or (len(clean_title) < 5 and len(clean_body) < 5):
                print(f"Skipping empty/spam post: '{clean_title}'")
                continue
                
            try:
                ai_data = analyze_post_with_gemini(title, body, token)
                # Safeguard cooldown to guarantee < 10 RPM (Free Tier Limit)
                time.sleep(8.0)
                updated_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                rows.append([created_date, ai_data["engineering_component"], ai_data["device"], ai_data["symptom"], ai_data["severity"], title, body, ai_data["summary_en"], ai_data["next_step"], full_url, updated_time, ai_data["remarks"]])
            except Exception as e:
                print(f"[Error] Row processing fallback triggered: {e}")
                rows.append([
                    created_date,
                    "General/Other",
                    "Unknown/Generic",
                    "Connectivity > Unknown",
                    "Minor",
                    title,
                    body,
                    "Unknown (Fallback)",
                    "Monitor trend scale",
                    full_url,
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "Exceeded Gemini API rate limit (Structured fallback applied)"
                ])
    return rows

def base64url_encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')

# 🛡️ 核心改動：純手工打造 JWT 身分驗證，完全不依賴外部 google-auth 套件
# 🛡️ 核心改動：修正 OpenSSL 標準輸入錯誤，改為直接使用臨時檔案簽章
def get_service_account_token_pure_python():
    print(f"Loading Service Account key from {SERVICE_ACCOUNT_FILE} (Pure Python Mode)...")
    try:
        with open(SERVICE_ACCOUNT_FILE, 'r') as f:
            sa_data = json.load(f)
        
        private_key = sa_data['private_key']
        client_email = sa_data['client_email']
        token_uri = sa_data.get('token_uri', 'https://oauth2.googleapis.com/token')
        
        # 1. 建立 JWT Header 與 Payload
        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        payload = {
            "iss": client_email,
            "scope": "https://www.googleapis.com/auth/spreadsheets https://www.googleapis.com/auth/cloud-platform",
            "aud": token_uri,
            "exp": now + 3600,
            "iat": now
        }
        
        # 2. Base64 處理
        encoded_header = base64url_encode(json.dumps(header).encode('utf-8'))
        encoded_payload = base64url_encode(json.dumps(payload).encode('utf-8'))
        signing_input = f"{encoded_header}.{encoded_payload}".encode('utf-8')
        
        # 3. 使用安全臨時檔案讓 OpenSSL 進行 RS256 簽章（避開 stdin `-` 的相容性問題）
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(mode='w+', delete=False) as tmp_key:
            tmp_key.write(private_key)
            tmp_key_name = tmp_key.name
            
        try:
            proc = subprocess.Popen(
                ['openssl', 'dgst', '-sha256', '-sign', tmp_key_name],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            signature, err = proc.communicate(input=signing_input)
        finally:
            # 確保不管簽章成功或失敗，都一定會把金鑰臨時檔刪除，保障安全
            if os.path.exists(tmp_key_name):
                os.unlink(tmp_key_name)
        
        if proc.returncode != 0:
            raise Exception(f"OpenSSL signature failed: {err.decode('utf-8')}")
        
        encoded_signature = base64url_encode(signature)
        jwt_token = f"{encoded_header}.{encoded_payload}.{encoded_signature}"
        
        # 4. 向 Google 換取 Access Token
        data = urllib.parse.urlencode({
            'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
            'assertion': jwt_token
        }).encode('utf-8')
        
        req = urllib.request.Request(token_uri, data=data, method='POST')
        with urllib.request.urlopen(req) as res:
            token_data = json.loads(res.read().decode('utf-8'))
            return token_data['access_token']
            
    except FileNotFoundError:
        print(f"Error: 找不到金鑰檔案 '{SERVICE_ACCOUNT_FILE}'。請確認檔案路徑。")
        return None
    except Exception as e:
        print(f"手工身分驗證發生錯誤: {e}")
        return None

def ensure_header_row(token):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/Sheet1!A1:L1?valueInputOption=USER_ENTERED"
    header_row = [["Date", "Engineering Component", "Device Impacted", "Symptom / Pain Point", "Severity", "Title", "Body/Snippet", "AI Summary", "Action / Next Steps", "Source", "Updated Time", "Remarks"]]
    payload = {
        "range": "Sheet1!A1:L1",
        "majorDimension": "ROWS",
        "values": header_row
    }
    req_body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=req_body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        method="PUT"
    )
    try:
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                print("Successfully ensured 11-column header alignment.")
    except Exception as e:
        print(f"Notice: Header check: {e}")

def append_to_google_sheet(token, rows):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{SHEET_RANGE}:append?valueInputOption=USER_ENTERED"
    
    payload = {
        "range": SHEET_RANGE,
        "majorDimension": "ROWS",
        "values": rows
    }
    
    req_body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=req_body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            if response.status == 200:
                print(f"Successfully appended {len(rows)} rows to Google Sheets.")
                return True
            else:
                print(f"Failed to append to Google Sheets: HTTP Status {response.status}")
                return False
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode("utf-8")
        print(f"HTTP Error appending to Google Sheets: {e.code} - {error_msg}")
        return False
    except urllib.error.URLError as e:
        print(f"Network error connecting to Google Sheets API: {e}")
        return False

def main():
    if not GEMINI_API_KEY:
        print("[Error] GEMINI_API_KEY is not set. Please copy .env.example to .env and configure your API key.")
        return
    if not SPREADSHEET_ID:
        print("[Error] SPREADSHEET_ID is not set. Please copy .env.example to .env and configure your Spreadsheet ID.")
        return

    cutoff_time = datetime.now(timezone.utc) - timedelta(days=14)
    rows = []
    
    token = get_auth_token()
    if not token:
        print("Could not retrieve access token. Aborting.")
        return
    
    posts = fetch_json_posts()
    if posts is not None:
        rows = process_json_posts(posts, cutoff_time, token)
    else:
        print("JSON API failed. Falling back to RSS feed...")
        entries, ns = fetch_rss_posts()
        if entries is not None and ns is not None:
            rows = process_rss_posts(entries, ns, cutoff_time, token)
        else:
            print("Both JSON and RSS feed fetches failed.")
            return

    if not rows:
        print("No posts matched the filter criteria (past 14 days).")
        return
        
    print(f"Filtered to {len(rows)} posts matching criteria.")
    
    ensure_header_row(token)
    append_to_google_sheet(token, rows)

if __name__ == "__main__":
    main()
