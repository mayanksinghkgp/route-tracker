# Route Travel Time Tracker

Periodically logs real-time travel times for a specific route using the Google Maps Directions API.  
Runs as a **Google Cloud Function** triggered by **Cloud Scheduler**, or locally via **Windows Task Scheduler**.

---

## How It Works

1. **Setup** — Run `setup_route.py` to pick an origin, destination, and a specific route (from up to 3 alternatives).
2. **Pinning** — The tool samples 3 intermediate coordinates from the route's polyline. These are stored as `via:` waypoints so every future API call follows the exact same road corridor.
3. **Tracking** — Every 30 minutes (configurable), the tracker queries the Directions API with `departure_time=now` and logs the current traffic-aware travel time to a CSV file.
4. **Analysis** — Download the CSV and analyze in Excel, Google Sheets, or Python.

### CSV Output

| Column | Description |
|:---|:---|
| `timestamp` | UTC ISO-8601 timestamp of the log entry |
| `route_id` | Short identifier (e.g., `home-to-office`) |
| `route_name` | Descriptive name |
| `origin` | Origin address |
| `destination` | Destination address |
| `distance_km` | Route distance in kilometres |
| `duration_seconds` | Baseline travel time (no traffic) in seconds |
| `duration_in_traffic_seconds` | Traffic-aware travel time in seconds |
| `duration_text` | Baseline travel time (human-readable) |
| `duration_in_traffic_text` | Traffic-aware travel time (human-readable) |
| `route_summary_returned` | Road/highway name returned by the API |

---

## Prerequisites

- **Python 3.10+**
- **Google Cloud account** (free tier is sufficient)
- **Google Cloud CLI** (`gcloud`) — [Install guide](https://cloud.google.com/sdk/docs/install)

---

## Step 1: Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click **Select a project** → **New Project**
3. Give it a name (e.g., `route-tracker`) and click **Create**
4. Note the **Project ID** (you'll need it later)

## Step 2: Enable Billing

> The **$200/month Maps Platform free credit** requires a billing account.  
> At ~1,440 API calls/month (every 30 min), you'll use about **$7–14** of the $200 credit. **Net cost: $0**.

1. In Cloud Console, go to **Billing** → **Link a billing account**
2. Add a payment method (you won't be charged within the free credit)

## Step 3: Enable the Directions API

1. Go to **APIs & Services** → **Library**
2. Search for **Directions API**
3. Click **Enable**

## Step 4: Create an API Key

1. Go to **APIs & Services** → **Credentials**
2. Click **+ Create Credentials** → **API Key**
3. Copy the key
4. **Recommended**: Click **Edit API Key** → Under **API restrictions**, select **Restrict key** → choose **Directions API** only

## Step 5: Set Up Locally

```powershell
# Clone/download the project
cd C:\Users\mayan\.gemini\antigravity\scratch\route-tracker

# Install dependencies
pip install -r requirements.txt

# Set your API key (for this session)
$env:GOOGLE_MAPS_API_KEY = "YOUR_API_KEY_HERE"
```

## Step 6: Configure a Route

```powershell
python setup_route.py
```

This will:
1. Ask for origin and destination — enter a **street address** or **lat,lng coordinates** (e.g. `28.6139, 77.2090`)
2. Show up to 3 route alternatives with distances, current travel times, and **clickable Google Maps links** so you can preview each route in a browser
3. Let you pick one
4. Save the route configuration to `config.json`

You can run this multiple times to add more routes.

## Step 7: Test Locally

```powershell
# Single run — logs one entry to a local CSV
python tracker.py --local
```

Check `travel_times.csv` in the project directory.

## Step 8: Run Continuously (Repeat Mode)

```powershell
# Default: poll every 30 min, start at the next 15-min clock boundary
python tracker.py --local --repeat

# Start immediately, poll every 15 min
python tracker.py --local --repeat --start-now --interval 15

# Stop after 48 polls
python tracker.py --local --repeat --start-now --max-sessions 48

# Only track 7 AM–8 PM, run for 7 days
python tracker.py --local --repeat --active-hours 7:00-20:00 --max-days 7

# Combine everything
python tracker.py --local --repeat --interval 15 --active-hours 6:30-22:00 --max-days 30 --max-sessions 500
```

| Flag | Default | Description |
|:---|:---|:---|
| `--repeat` | off | Enable continuous polling mode |
| `--interval N` | 30 | Minutes between polls |
| `--start-now` | off | Start immediately (default: wait for next 15-min boundary) |
| `--max-sessions N` | unlimited | Stop after N polls |
| `--max-days N` | unlimited | Stop after N calendar days |
| `--active-hours HH:MM-HH:MM` | all day | Only poll during this daily window |
| `--routes ID[,ID,...]` | all | Comma-separated route IDs to track |
| `--list-routes` | — | List all configured routes and exit |

**Tracking specific routes:**
```powershell
# See what's configured
python tracker.py --list-routes

# Track only one route
python tracker.py --local --routes home-to-office

# Track two specific routes
python tracker.py --local --repeat --routes home-to-office,office-to-gym
```

## Step 9: Plot Travel Times

```powershell
# Plot all data for a route
python plot_route.py --route home-to-office

# Custom CSV path
python plot_route.py --route home-to-office --csv travel_times.csv

# Save to file instead of showing
python plot_route.py --route home-to-office --output commute_chart.png
```

This generates a scatter plot of **travel time vs. hour of day** (15-min granularity on the x-axis), with each day shown as a separate colour so you can see patterns and trends.

---

## Deployment Options

### Option A: Google Cloud Functions (Recommended)

Runs 24/7 without your machine being on.

```powershell
# Authenticate with Google Cloud
gcloud auth login
gcloud auth application-default login

# Deploy everything
.\deploy.ps1 -ProjectId "your-project-id" -ApiKey "AIzaSy..."

# Custom schedule (every 15 minutes)
.\deploy.ps1 -ProjectId "your-project-id" -ApiKey "AIzaSy..." -Schedule "*/15 * * * *"
```

**Download your logs anytime:**
```powershell
gsutil cp gs://your-project-id-route-tracker/travel_times.csv .
```

### Option B: Windows Task Scheduler (Local)

Runs only when your machine is on. No cloud setup needed (just the API key).

1. **Create a batch file** (`run_tracker.bat`):
   ```batch
   @echo off
   set GOOGLE_MAPS_API_KEY=YOUR_API_KEY_HERE
   cd /d C:\Users\mayan\.gemini\antigravity\scratch\route-tracker
   python tracker.py --local
   ```

2. **Create a scheduled task:**
   ```powershell
   $action = New-ScheduledTaskAction `
       -Execute "C:\Users\mayan\.gemini\antigravity\scratch\route-tracker\run_tracker.bat"
   
   $trigger = New-ScheduledTaskTrigger `
       -Once -At (Get-Date) `
       -RepetitionInterval (New-TimeSpan -Minutes 30) `
       -RepetitionDuration (New-TimeSpan -Days 365)
   
   Register-ScheduledTask `
       -TaskName "RouteTracker" `
       -Action $action `
       -Trigger $trigger `
       -Description "Log route travel times every 30 minutes"
   ```

3. **Verify it's running:**
   ```powershell
   Get-ScheduledTask -TaskName "RouteTracker"
   ```

---

## Managing Routes

### Add another route
```powershell
python setup_route.py
```

### View current routes
```powershell
python -c "import json; c=json.load(open('config.json')); [print(f'  {r[\"id\"]}: {r[\"name\"]}') for r in c['routes']]"
```

### Remove a route
Edit `config.json` and delete the route entry from the `routes` array. Then re-upload if using Cloud Functions:
```powershell
gsutil cp config.json gs://your-bucket-name/config.json
```

---

## Cost Summary

| Service | Free Tier | Your Usage | Cost |
|:---|:---|:---|:---|
| Directions API | $200/month credit | ~$7–14/month (1 route, 30 min interval) | **$0** |
| Cloud Functions | 2M invocations/month | ~1,440/month | **$0** |
| Cloud Scheduler | 3 jobs/month | 1 job | **$0** |
| Cloud Storage | 5 GB/month | <1 MB | **$0** |

---

## Troubleshooting

| Error | Cause | Fix |
|:---|:---|:---|
| `REQUEST_DENIED` | API key invalid or Directions API not enabled | Check API key and enable the Directions API in Cloud Console |
| `OVER_QUERY_LIMIT` | Quota exceeded | Check your quotas in Cloud Console → APIs & Services |
| `ZERO_RESULTS` | No route found | Verify the origin/destination addresses are valid |
| Config not found | `config.json` missing | Run `python setup_route.py` to create it |

---

## Project Structure

```
route-tracker/
├── README.md           <- This file
├── requirements.txt    <- Python dependencies
├── config.json         <- Route configuration (generated by setup)
├── tracker.py          <- Core logic (API calls, CSV logging, polyline decoder)
├── setup_route.py      <- Interactive CLI for route selection
├── plot_route.py       <- Plot travel time vs. hour of day
├── main.py             <- Cloud Function entry point
└── deploy.ps1          <- PowerShell deployment script
```
