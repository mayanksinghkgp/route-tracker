"""
Plot Route Travel Times

Reads the tracker CSV and plots travel time vs. hour of day for a given route.
X-axis: hour of day with 15-minute granularity (e.g. 07:00, 07:15, ... 22:45)
Y-axis: travel time in minutes (uses duration_in_traffic when available)

Each date is plotted as a separate colour so you can spot day-to-day patterns.
"""

import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime

try:
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import matplotlib.ticker as ticker
except ImportError:
    print("Error: matplotlib is required.  Install it with:")
    print("  pip install matplotlib")
    sys.exit(1)


def load_csv(csv_path: str, route_id: str) -> list[dict]:
    """
    Reads the CSV and returns rows matching the given route_id.
    Parses the timestamp and computes travel time in minutes.
    """
    if not os.path.exists(csv_path):
        print(f"Error: CSV file not found at {csv_path}")
        sys.exit(1)

    rows: list[dict] = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["route_id"] != route_id:
                continue

            # Parse timestamp (IST with offset, e.g. 2026-09-14T08:30:00+05:30)
            try:
                ts = datetime.fromisoformat(row["timestamp"])
            except ValueError:
                continue

            # Use traffic duration if available, else baseline
            traffic_secs = row.get("duration_in_traffic_seconds", "")
            baseline_secs = row.get("duration_seconds", "")
            if traffic_secs and traffic_secs not in ("", "None"):
                minutes = float(traffic_secs) / 60.0
            elif baseline_secs:
                minutes = float(baseline_secs) / 60.0
            else:
                continue

            rows.append({
                "timestamp": ts,
                "date": ts.strftime("%Y-%m-%d"),
                "hour_decimal": ts.hour + ts.minute / 60.0,
                "time_of_day": ts.strftime("%H:%M"),
                "minutes": minutes,
            })

    return rows


def plot(rows: list[dict], route_id: str, output_path: str | None) -> None:
    """
    Creates a scatter plot of travel time vs. hour of day.
    Each date gets its own colour.
    """
    if not rows:
        print(f"No data found for route '{route_id}'.")
        sys.exit(1)

    # Group by date
    by_date: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append(r)

    dates_sorted = sorted(by_date.keys())

    # Set up figure
    fig, ax = plt.subplots(figsize=(14, 6))
    cmap = plt.colormaps.get_cmap("tab10").resampled(max(len(dates_sorted), 1))

    for i, date_str in enumerate(dates_sorted):
        day_rows = by_date[date_str]
        xs = [r["hour_decimal"] for r in day_rows]
        ys = [r["minutes"] for r in day_rows]
        # Format weekday for legend
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        label = dt.strftime("%a %d %b")
        ax.scatter(xs, ys, color=cmap(i), label=label, s=50, alpha=0.8, edgecolors="white", linewidth=0.5)
        # Connect points within the same day
        if len(xs) > 1:
            sorted_pairs = sorted(zip(xs, ys))
            ax.plot([p[0] for p in sorted_pairs], [p[1] for p in sorted_pairs],
                    color=cmap(i), alpha=0.4, linewidth=1)

    # X-axis: 15-min ticks from first to last observed hour
    min_hour = int(min(r["hour_decimal"] for r in rows))
    max_hour = int(max(r["hour_decimal"] for r in rows)) + 1
    xticks = [h + m / 60.0 for h in range(min_hour, max_hour + 1) for m in (0, 15, 30, 45)]
    xtick_labels = [f"{int(t)}:{int((t % 1) * 60):02d}" for t in xticks]

    # Show labels only on the hour to avoid clutter
    display_labels = [lbl if lbl.endswith(":00") else "" for lbl in xtick_labels]

    ax.set_xticks(xticks)
    ax.set_xticklabels(display_labels, rotation=45, ha="right", fontsize=8)
    ax.xaxis.set_minor_locator(ticker.FixedLocator(xticks))
    ax.grid(True, which="major", axis="x", alpha=0.3)
    ax.grid(True, which="minor", axis="x", alpha=0.1)
    ax.grid(True, which="major", axis="y", alpha=0.3)

    ax.set_xlabel("Time of Day", fontsize=11)
    ax.set_ylabel("Travel Time (minutes)", fontsize=11)
    ax.set_title(f"Travel Time by Hour of Day  —  {route_id}", fontsize=13)

    # Legend outside plot if many days, else inside
    if len(dates_sorted) <= 7:
        ax.legend(fontsize=8, loc="upper left")
    else:
        ax.legend(fontsize=7, loc="upper left", bbox_to_anchor=(1.01, 1), borderaxespad=0)
        fig.subplots_adjust(right=0.82)

    plt.tight_layout()

    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches="tight")
        print(f"Chart saved to {output_path}")
    else:
        plt.show()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot travel time vs. hour of day for a tracked route",
    )
    parser.add_argument(
        "--route",
        required=True,
        help="Route ID to plot (as configured in config.json)",
    )
    parser.add_argument(
        "--csv",
        default="travel_times.csv",
        help="Path to the tracker CSV file (default: travel_times.csv)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Save chart to this file (e.g. chart.png) instead of showing interactively",
    )
    args = parser.parse_args()

    # Resolve CSV path
    csv_path = args.csv
    if not os.path.isabs(csv_path):
        csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), csv_path)

    rows = load_csv(csv_path, args.route)
    print(f"Loaded {len(rows)} data points for route '{args.route}' "
          f"across {len(set(r['date'] for r in rows))} day(s).")

    plot(rows, args.route, args.output)


if __name__ == "__main__":
    main()
