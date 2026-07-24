# Reddit Chromecast Tracking

A lightweight tool that monitors `r/chromecast` for user issues, uses Google Gemini AI to triage and classify them, and displays the insights on a web dashboard.

## Why This Project?

Reddit has become one of the earliest places where Chromecast users report real-world issues. In many cases, discussions appear on Reddit before official support tickets are filed, making it a valuable source for early issue detection.

This project helps engineering teams automatically monitor Chromecast-related Reddit discussions to:

- Detect newly emerging issues and regressions.
- Identify recurring problems reported by multiple users.
- Estimate the impact of an issue based on community activity.
- Collect debugging information such as device models, firmware versions, app versions, and reproduction steps.
- Support issue prioritization with real user feedback.
- Enable community support teams to respond more quickly and gather additional diagnostic information.

By continuously tracking Reddit discussions, engineering and support teams can discover potential product issues earlier and make more informed decisions about investigation and prioritization.

---
## 1. What This Project Is

- **Backend**: A Python script that fetches recent posts from `r/chromecast`, uses **Google Gemini AI** to summarize and categorize issues (component, device impact, severity), and logs the reports to a Google Sheet.
- **Frontend**: A clean, responsive single-page web dashboard that reads live data from the Google Sheet and displays issue statistics, severity breakdowns, and detailed triage reports.

### Reddit Data Sync & Fallback Logic

The script tracks r/chromecast posts from the **past 14 days** using a resilient, two-tiered fetching strategy:

1. **Primary (JSON API)**: Queries `/new.json?limit=100` to fetch up to 100 of the latest posts.
2. **Fallback (RSS Feed)**: If the JSON API is rate-limited (HTTP 403), it immediately falls back to `/new/.rss` (capped by Reddit at 25 posts).
3. The `r/chromecast` subreddit averages **20–30 posts every two weeks**.
---

## 2. Folder Structure

```text
reddit-chromecast-tracking/
├── backend/
│   └── fetch_reddit_cast_public.py  # Scraper & Gemini AI triage script
├── frontend/
│   ├── index.html                   # Standalone web dashboard
│   └── config.example.js            # Example frontend configuration variables
├── .env.example                     # Example backend configuration variables
├── .gitignore                       # Git ignore rules for sensitive files
└── README.md                        # Project documentation
```

---

## 3. Configuration Setup

To keep sensitive credentials secure when pushing to GitHub:

1. **Backend Configuration**:
   ```bash
   cp .env.example .env
   ```
   Open `.env` and fill in your `GEMINI_API_KEY`, `SPREADSHEET_ID`, and the path to your Google Service Account `.json` file (`GOOGLE_APPLICATION_CREDENTIALS`).

2. **Frontend Configuration**:
   ```bash
   cp frontend/config.example.js frontend/config.js
   ```
   Open `frontend/config.js` and fill in your `SHEET_ID` and `API_KEY`.

3. **Security check**: Both `.env` and `frontend/config.js` (along with Google credential `.json` files) are excluded in `.gitignore` so your secrets never get committed to GitHub.

---

## 4. How to Run

### Backend

1. Install required Python packages:
   ```bash
   python3 -m pip install google-genai python-dotenv
   ```
2. Run the data pipeline from the project root:
   ```bash
   python3 backend/fetch_reddit_cast_public.py
   ```

### Frontend

Start a local static server from the project root:
```bash
python3 -m http.server 8000
```
Open [http://localhost:8000/frontend/](http://localhost:8000/frontend/) in your browser.
