"""
Route Setup CLI

Interactive tool to select an origin, destination, and specific route.
Saves the configuration to config.json for use by the tracker.
"""

import json
import os
import sys

from urllib.parse import quote

from tracker import decode_polyline, fetch_alternative_routes, sample_pin_points


def get_input(prompt: str, default: str = "") -> str:
    """Get user input with an optional default value."""
    if default:
        result = input(f"{prompt} [{default}]: ").strip()
        return result if result else default
    return input(f"{prompt}: ").strip()


def parse_location(raw: str) -> str:
    """
    Accepts either a text address or ``lat,lng`` coordinates.

    If the input looks like valid coordinates (two comma-separated numbers
    within geographic bounds), returns them normalised as ``"lat,lng"``.
    Otherwise returns the raw text as-is for geocoding by the API.
    """
    raw = raw.strip()
    parts = raw.split(",")
    if len(parts) == 2:
        try:
            lat = float(parts[0].strip())
            lng = float(parts[1].strip())
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                return f"{lat},{lng}"
        except ValueError:
            pass
    return raw


def build_maps_url(origin: str, destination: str, route: dict) -> str:
    """
    Builds a Google Maps Directions URL with via-waypoints so the
    browser opens a view that closely matches the given route alternative.
    """
    encoded_polyline = route["overview_polyline"]["points"]
    coords = decode_polyline(encoded_polyline)
    n = len(coords)

    # Sample 2 waypoints to nudge Maps toward this specific corridor
    if n >= 4:
        wp1 = coords[int(n * 0.33)]
        wp2 = coords[int(n * 0.66)]
        waypoints = f"{wp1[0]:.6f},{wp1[1]:.6f}|{wp2[0]:.6f},{wp2[1]:.6f}"
    elif n >= 3:
        wp = coords[int(n * 0.5)]
        waypoints = f"{wp[0]:.6f},{wp[1]:.6f}"
    else:
        waypoints = ""

    url = (
        f"https://www.google.com/maps/dir/?api=1"
        f"&origin={quote(origin)}"
        f"&destination={quote(destination)}"
        f"&travelmode=driving"
    )
    if waypoints:
        url += f"&waypoints={quote(waypoints)}"
    return url


def display_routes(routes: list[dict], origin: str, destination: str) -> None:
    """Display route alternatives with clickable Google Maps links."""
    print(f"\n{'=' * 70}")
    print(f"  Found {len(routes)} route option(s):")
    print(f"{'=' * 70}")

    for idx, route in enumerate(routes):
        leg = route["legs"][0]
        summary = route.get("summary", "Unknown")
        distance = leg["distance"]["text"]
        duration = leg["duration"]["text"]
        traffic = leg.get("duration_in_traffic", {}).get("text", "N/A")
        maps_url = build_maps_url(origin, destination, route)

        print(f"\n  [{idx + 1}] {summary}")
        print(f"      Distance:         {distance}")
        print(f"      Duration:         {duration}")
        print(f"      With traffic:     {traffic}")
        print(f"      View in Maps:     {maps_url}")

    print(f"\n{'=' * 70}")
    print("  Tip: Open the 'View in Maps' links in a browser to preview each route.")


def load_config(config_path: str) -> dict:
    """Load existing config or return a default structure."""
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "routes": [],
        "gcs_bucket": "",
        "csv_filename": "travel_times.csv",
    }


def save_config(config: dict, config_path: str) -> None:
    """Save config dictionary to a JSON file."""
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    print(f"\n  [OK] Configuration saved to {config_path}")


def main() -> None:
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

    print()
    print("╔══════════════════════════════════════════╗")
    print("║    Route Travel Time Tracker — Setup     ║")
    print("╚══════════════════════════════════════════╝")

    # ---- API Key ----
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
    if not api_key:
        api_key = get_input("\nEnter your Google Maps API key")
    else:
        print(f"\n  Using API key from GOOGLE_MAPS_API_KEY environment variable.")

    if not api_key:
        print("  Error: API key is required.")
        sys.exit(1)

    # ---- Origin / Destination ----
    print("\n--- Route Details ---")
    print("  (You can enter a street address OR lat,lng coordinates)")
    origin_raw = get_input("  Origin")
    destination_raw = get_input("  Destination")

    if not origin_raw or not destination_raw:
        print("  Error: Both origin and destination are required.")
        sys.exit(1)

    origin = parse_location(origin_raw)
    destination = parse_location(destination_raw)

    # ---- Fetch Alternatives ----
    print(f"\n  Fetching routes from '{origin}' to '{destination}'...")
    try:
        routes = fetch_alternative_routes(origin, destination, api_key)
    except Exception as e:
        print(f"  Error fetching routes: {e}")
        sys.exit(1)

    if not routes:
        print("  No routes found. Check your addresses and try again.")
        sys.exit(1)

    # ---- Display & Select ----
    display_routes(routes, origin, destination)

    while True:
        choice = get_input(f"\n  Select a route (1–{len(routes)})")
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(routes):
                break
        except ValueError:
            pass
        print(f"  Please enter a number between 1 and {len(routes)}.")

    selected = routes[idx]

    # ---- Route ID and Name ----
    config = load_config(config_path)
    default_id = f"route-{len(config['routes']) + 1}"
    default_name = (
        f"{origin[:25]} → {destination[:25]} via {selected.get('summary', 'default')}"
    )

    route_id = get_input("  Route ID (short, no spaces)", default_id)
    route_name = get_input("  Route name (descriptive)", default_name)

    # ---- Extract Pin Points ----
    polyline = selected["overview_polyline"]["points"]

    pin_count_str = get_input(
        "  Number of pinning waypoints (more = more consistent distance, 3-10)", "5"
    )
    try:
        pin_count = max(3, min(10, int(pin_count_str)))
    except ValueError:
        pin_count = 5

    pin_points = sample_pin_points(polyline, count=pin_count)

    print(f"\n  [OK] Route selected: {selected.get('summary', 'Custom Route')}")
    print(f"    Sampled {len(pin_points)} pinning waypoints for route corridor.")

    # ---- Build Config Entry ----
    route_entry = {
        "id": route_id,
        "name": route_name,
        "origin": origin,
        "destination": destination,
        "overview_polyline": polyline,
        "pin_points": pin_points,
    }

    # ---- Check for Duplicate ID ----
    existing_ids = [r["id"] for r in config["routes"]]
    if route_id in existing_ids:
        overwrite = get_input(
            f"  Route ID '{route_id}' already exists. Overwrite? (y/n)", "n"
        )
        if overwrite.lower() == "y":
            config["routes"] = [r for r in config["routes"] if r["id"] != route_id]
        else:
            print("  Aborted.")
            sys.exit(0)

    config["routes"].append(route_entry)

    # ---- Schedule Configuration ----
    # Only prompt if no schedule exists yet (first route setup)
    if "schedule" not in config:
        print("\n--- Tracking Schedule ---")
        print("  (These become defaults for 'python tracker.py --local --repeat')")

        interval = get_input("  Polling interval in minutes", "30")
        try:
            interval = int(interval)
        except ValueError:
            interval = 30

        active_hours = get_input(
            "  Active hours (e.g. '7:00-20:00', or leave empty for all day)", ""
        )

        max_days_str = get_input(
            "  Stop after N days (or leave empty for unlimited)", ""
        )
        max_days = int(max_days_str) if max_days_str else None

        max_sessions_str = get_input(
            "  Stop after N sessions (or leave empty for unlimited)", ""
        )
        max_sessions = int(max_sessions_str) if max_sessions_str else None

        start_aligned = get_input(
            "  Align start to next 15-min boundary? (y/n)", "y"
        ).lower() == "y"

        config["schedule"] = {
            "interval_minutes": interval,
            "active_hours": active_hours or None,
            "max_days": max_days,
            "max_sessions": max_sessions,
            "start_aligned": start_aligned,
        }
    else:
        sched = config["schedule"]
        ah = sched.get('active_hours', '')
        print(f"\n  Using existing schedule: every {sched['interval_minutes']} min"
              f"{f', active {ah}' if ah else ''}")

    # ---- GCS Bucket (optional) ----
    if not config.get("gcs_bucket"):
        print("\n--- Cloud Storage Configuration ---")
        bucket = get_input(
            "  GCS bucket name (leave empty to configure later)", ""
        )
        config["gcs_bucket"] = bucket

    save_config(config, config_path)

    # ---- Summary ----
    sched = config.get("schedule", {})
    ah = sched.get('active_hours', '')
    md = sched.get('max_days')
    ms = sched.get('max_sessions')
    print(f"\n{'=' * 60}")
    print("  Setup Complete!")
    print(f"  Routes configured: {len(config['routes'])}")
    for r in config["routes"]:
        print(f"    - {r['id']}: {r['name']}")
    print()
    print(f"  Schedule: every {sched.get('interval_minutes', 30)} min"
          f"{f', {ah}' if ah else ', all day'}"
          f"{f', max {md}d' if md else ''}"
          f"{f', max {ms} sessions' if ms else ''}")
    print()
    print("  Next steps:")
    print("    1. Test single shot:  python tracker.py --local")
    print("    2. Run with schedule: python tracker.py --local --repeat")
    print("    3. Track one route:   python tracker.py --local --repeat --routes route-1")
    print("    4. Deploy to cloud:   .\\deploy.ps1 -ProjectId <id> -ApiKey <key>")
    print(f"{'=' * 60}")
    print()


if __name__ == "__main__":
    main()
