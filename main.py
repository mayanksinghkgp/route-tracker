"""
Cloud Function Entry Point

HTTP-triggered function that serves two purposes:
1. POST (Cloud Scheduler) — Tracks travel times and logs to CSV in GCS.
2. GET  (Browser/PWA)     — Serves the mobile dashboard and data API.

Routes:
    POST /            — Run tracker (existing Cloud Scheduler behavior)
    GET  /            — Serve the dashboard HTML
    GET  /api/data    — Return CSV data as JSON (supports ?route_id=...&days=...)
    GET  /api/routes  — Return route metadata from config.json

Access control:
    Set DASHBOARD_KEY env var to require ?key=<value> on all GET requests.
    When unset, access is open.
"""

import json
import os

from datetime import datetime

import functions_framework
from flask import make_response
from google.cloud import storage

from tracker import track_all_routes
from dashboard_api import get_csv_data, get_route_list


# ---------------------------------------------------------------------------
# Access Control
# ---------------------------------------------------------------------------

def _check_access_key(request) -> str | None:
    """
    If DASHBOARD_KEY is set, validates the 'key' query parameter.
    Returns an error message string if access is denied, or None if OK.
    """
    expected_key = os.environ.get("DASHBOARD_KEY", "")
    if not expected_key:
        return None  # No key configured — open access

    provided_key = request.args.get("key", "")
    if provided_key != expected_key:
        return "Unauthorized. Provide ?key=<your-key> to access the dashboard."
    return None


# ---------------------------------------------------------------------------
# Dashboard HTML
# ---------------------------------------------------------------------------

_dashboard_html_cache: str | None = None

def _get_dashboard_html() -> str:
    """Load and cache the dashboard HTML file."""
    global _dashboard_html_cache
    if _dashboard_html_cache is None:
        html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
        with open(html_path, "r", encoding="utf-8") as f:
            _dashboard_html_cache = f.read()
    return _dashboard_html_cache


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

@functions_framework.http
def track_routes(request):
    """
    HTTP Cloud Function entry point.

    POST requests (from Cloud Scheduler) run the tracker.
    GET requests serve the dashboard and data API.

    Required environment variables:
        GOOGLE_MAPS_API_KEY  — Directions API key.
        GCS_BUCKET           — Cloud Storage bucket name.
        CONFIG_FILENAME      — Config file name in the bucket (default: config.json).
    Optional environment variables:
        DASHBOARD_KEY        — Access key for GET requests (default: open).
    """

    # ---- POST: Tracking (Cloud Scheduler) ----
    if request.method == "POST":
        return _handle_tracking(request)

    # ---- GET: Dashboard & API ----
    if request.method == "GET":
        # Check access key for all GET requests
        auth_error = _check_access_key(request)
        if auth_error:
            return (auth_error, 403)

        path = request.path.rstrip("/")

        if path == "/api/data":
            return _handle_api_data(request)
        elif path == "/api/routes":
            return _handle_api_routes(request)
        else:
            return _handle_dashboard(request)

    return ("Method not allowed", 405)


# ---------------------------------------------------------------------------
# POST Handler — Tracking (unchanged logic)
# ---------------------------------------------------------------------------

def _handle_tracking(request):
    """Original tracking logic — triggered by Cloud Scheduler."""
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not api_key:
        return ("GOOGLE_MAPS_API_KEY environment variable not set", 500)

    bucket_name = os.environ.get("GCS_BUCKET")
    config_filename = os.environ.get("CONFIG_FILENAME", "config.json")

    if not bucket_name:
        return ("GCS_BUCKET environment variable not set", 500)

    # Read config from Cloud Storage
    try:
        client = storage.Client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(config_filename)
        config_text = blob.download_as_text(encoding="utf-8")
        config = json.loads(config_text)
    except Exception as e:
        return (f"Failed to read config from gs://{bucket_name}/{config_filename}: {e}", 500)

    # Ensure the bucket is set in the config for GCS logging
    config["gcs_bucket"] = bucket_name

    # Track all routes (evaluating per-route active hours) and log to CSV
    results = track_all_routes(config, api_key, check_active_hours=True)

    # Build a human-readable response
    lines = []
    tracked_count = 0
    for r in results:
        if r["status"] == "ok":
            lines.append(f"✓ {r['route_id']}: {r['data']['duration_in_traffic_text']}")
            tracked_count += 1
        elif r["status"] == "skipped":
            lines.append(f"- {r['route_id']}: Skipped ({r.get('reason', 'outside active hours')})")
        else:
            lines.append(f"✗ {r['route_id']}: {r.get('error')}")

    status_header = f"[{tracked_count}/{len(results)} routes tracked]"
    return (f"{status_header}\n" + "\n".join(lines), 200)


# ---------------------------------------------------------------------------
# GET Handlers — Dashboard & API
# ---------------------------------------------------------------------------

def _handle_dashboard(request):
    """Serve the self-contained HTML dashboard."""
    try:
        html = _get_dashboard_html()
        response = make_response(html, 200)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.headers["Cache-Control"] = "public, max-age=300"
        return response
    except FileNotFoundError:
        return ("Dashboard HTML not found", 500)


def _handle_api_data(request):
    """Return CSV data as JSON, with optional route_id and days filters."""
    bucket_name = os.environ.get("GCS_BUCKET")
    csv_filename = os.environ.get("CSV_FILENAME", "travel_times.csv")

    if not bucket_name:
        return _json_error("GCS_BUCKET environment variable not set", 500)

    route_id = request.args.get("route_id")
    days_str = request.args.get("days")
    days = int(days_str) if days_str and days_str.isdigit() and int(days_str) > 0 else None

    try:
        data = get_csv_data(bucket_name, csv_filename, route_id=route_id, days=days)
        return _json_response(data)
    except Exception as e:
        return _json_error(f"Failed to read data: {e}", 500)


def _handle_api_routes(request):
    """Return route metadata from config.json."""
    bucket_name = os.environ.get("GCS_BUCKET")
    config_filename = os.environ.get("CONFIG_FILENAME", "config.json")

    if not bucket_name:
        return _json_error("GCS_BUCKET environment variable not set", 500)

    try:
        routes = get_route_list(bucket_name, config_filename)
        return _json_response(routes)
    except Exception as e:
        return _json_error(f"Failed to read routes: {e}", 500)


# ---------------------------------------------------------------------------
# Response Helpers
# ---------------------------------------------------------------------------

def _json_response(data, status=200):
    """Create a JSON response with proper headers."""
    response = make_response(json.dumps(data, ensure_ascii=False), status)
    response.headers["Content-Type"] = "application/json; charset=utf-8"
    response.headers["Cache-Control"] = "public, max-age=60"
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


def _json_error(message: str, status: int):
    """Create a JSON error response."""
    return _json_response({"error": message}, status)
