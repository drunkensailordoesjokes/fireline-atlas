"""NASA FIRMS hotspot calendar MVP.

Upload MODIS and/or VIIRS active-fire CSV files exported from NASA FIRMS.
The app normalizes the records for a shared map and daily activity calendar.
"""

from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

import pandas as pd
import plotly.express as px
import streamlit as st
import streamlit.components.v1 as components
from map_sampling import sample_map_points
from map_area import coordinate_search_box
from globe_camera import GLOBE_CAMERA_SESSION_KEY, capture_globe_camera


st.set_page_config(page_title="Fireline Atlas | Hotspot Calendar", page_icon="🔥", layout="wide")
MAP_POINT_LIMIT = 2_500
FIRMS_MAX_DAYS_PER_REQUEST = 5
_radius_control_component = components.declare_component(
    "fireline_radius_control",
    path=str(Path(__file__).parent / "components" / "radius_control"),
)

st.title("🔥 Fireline Atlas")
st.caption(
    "A sensor-aware calendar of satellite fire and thermal-anomaly detections."
)

with st.expander("What do these detections mean?", expanded=True):
    st.markdown(
        """
        - **A hotspot is a satellite observation:** it marks a pixel where the sensor detected unusual heat during an overpass. It does not draw a fire boundary or confirm that a fire is still burning.
        - **The sensors see different-sized pixels:** FIRMS describes MODIS detections at about 1 km and VIIRS at about 375 m. VIIRS can detect some smaller or less intense fires that MODIS may miss, so their raw counts are not directly interchangeable.
        - **A detection is not always a wildfire:** heat can come from agricultural burning or other sources. Clouds, smoke, tree cover, and the timing of satellite passes can also affect what is detected.

        This app helps explore patterns; it is not an emergency alert or a fire-spread forecast.
        """
    )

with st.expander("Getting started: NASA key or CSV files?"):
    live_col, csv_col = st.columns(2)
    with live_col:
        st.markdown("**Option 1: Load recent data with a FIRMS key**")
        st.markdown(
            "1. Request a free NASA FIRMS MAP_KEY with your email. NASA sends the key by email.\n"
            "2. Paste it into **Optional FIRMS map key** in the left sidebar.\n"
            "3. Search around a latitude/longitude or choose the whole globe, pick 1–5 days, and load the data."
        )
        st.link_button(
            "Request a free FIRMS key",
            "https://firms.modaps.eosdis.nasa.gov/api/map_key/",
        )
        st.caption(
            "The key is used to request data from NASA FIRMS. Keep it private and never add it to GitHub."
        )

    with csv_col:
        st.markdown("**Option 2: Upload FIRMS CSV files (no key needed)**")
        st.markdown(
            "1. Download a MODIS CSV, a VIIRS CSV, or both from NASA FIRMS.\n"
            "2. For older dates, use FIRMS Archive Download and choose CSV.\n"
            "3. Upload each file in its matching box in the left sidebar."
        )
        data_link_col, archive_link_col = st.columns(2)
        with data_link_col:
            st.link_button(
                "Recent FIRMS CSVs",
                "https://firms.modaps.eosdis.nasa.gov/content/active_fire/",
            )
        with archive_link_col:
            st.link_button(
                "Older dates (Archive)",
                "https://firms.modaps.eosdis.nasa.gov/download/",
            )
        st.caption(
            "Some downloads ask you to sign in. The CSV needs latitude, longitude, and acquisition date columns."
        )


def normalize_csv(file: BytesIO, sensor_label: str) -> pd.DataFrame:
    """Read a FIRMS CSV and map common column names into a shared schema."""
    frame = pd.read_csv(file)
    frame.columns = [str(column).strip().lower() for column in frame.columns]

    aliases = {
        "latitude": ("latitude", "lat"),
        "longitude": ("longitude", "lon", "long"),
        "date": ("acq_date", "date", "acquisition_date"),
        "time": ("acq_time", "time", "acquisition_time"),
        "confidence": ("confidence", "conf"),
        "brightness": ("bright_ti4", "brightness", "bright_t31", "bright_ti5"),
    }

    def find_column(names: tuple[str, ...]) -> str | None:
        return next((name for name in names if name in frame.columns), None)

    lat_col = find_column(aliases["latitude"])
    lon_col = find_column(aliases["longitude"])
    date_col = find_column(aliases["date"])
    if not lat_col or not lon_col or not date_col:
        raise ValueError("CSV needs latitude, longitude, and acq_date columns.")

    result = pd.DataFrame(
        {
            "latitude": pd.to_numeric(frame[lat_col], errors="coerce"),
            "longitude": pd.to_numeric(frame[lon_col], errors="coerce"),
            "date": pd.to_datetime(frame[date_col], errors="coerce").dt.date,
            "sensor": sensor_label,
        }
    )

    time_col = find_column(aliases["time"])
    if time_col:
        result["time"] = frame[time_col].astype("string")
    confidence_col = find_column(aliases["confidence"])
    if confidence_col:
        result["confidence"] = frame[confidence_col].astype("string")
    brightness_col = find_column(aliases["brightness"])
    if brightness_col:
        result["brightness"] = pd.to_numeric(frame[brightness_col], errors="coerce")

    return result.dropna(subset=["latitude", "longitude", "date"])


def bounding_box_for_data(frame: pd.DataFrame) -> str:
    """Return a padded FIRMS west,south,east,north box around loaded points."""
    padding = 0.05
    west = max(-180.0, float(frame["longitude"].min()) - padding)
    south = max(-90.0, float(frame["latitude"].min()) - padding)
    east = min(180.0, float(frame["longitude"].max()) + padding)
    north = min(90.0, float(frame["latitude"].max()) + padding)
    return f"{west:.4f},{south:.4f},{east:.4f},{north:.4f}"


@st.cache_data(ttl=600, show_spinner=False)
def load_live_area(
    map_key: str, area_coordinates: str, day_range: int
) -> tuple[pd.DataFrame, list[str]]:
    """Fetch recent MODIS and VIIRS detections from FIRMS for an area."""
    sources = [
        ("MODIS_NRT", "MODIS"),
        ("VIIRS_NOAA20_NRT", "VIIRS"),
        ("VIIRS_NOAA21_NRT", "VIIRS"),
    ]
    frames: list[pd.DataFrame] = []
    failures: list[str] = []

    for source, sensor in sources:
        url = (
            "https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
            f"{quote(map_key, safe='')}/{source}/{area_coordinates}/{day_range}"
        )
        try:
            with urlopen(url, timeout=30) as response:
                payload = response.read()
            frame = normalize_csv(BytesIO(payload), sensor)
            frame["source"] = source
            frames.append(frame)
        except HTTPError as exc:
            response_text = exc.read().decode("utf-8", errors="replace").strip()
            # Never echo the key back into the page if an upstream error reflects it.
            response_text = response_text.replace(map_key, "[hidden key]")
            encoded_key = quote(map_key, safe="")
            response_text = response_text.replace(encoded_key, "[hidden key]")
            detail = response_text[:240] or str(exc.reason)
            failures.append(f"{source}: NASA returned HTTP {exc.code}: {detail}")
        except Exception as exc:
            failures.append(f"{source}: {exc}")

    if frames:
        return pd.concat(frames, ignore_index=True), failures
    raise RuntimeError("No FIRMS sensor data could be loaded. " + "; ".join(failures))


@st.cache_data(ttl=600, show_spinner=False)
def load_historical_area(
    map_key: str,
    first_day: str,
    day_range: int,
    area_coordinates: str,
    sensors: tuple[str, ...] = ("MODIS", "VIIRS"),
) -> tuple[pd.DataFrame, list[str]]:
    """Fetch a historical FIRMS window in chunks and only for selected sensors."""
    sources_by_sensor = {
        "MODIS": [("MODIS_SP", "MODIS")],
        "VIIRS": [
            ("VIIRS_NOAA20_SP", "VIIRS"),
            ("VIIRS_NOAA21_SP", "VIIRS"),
        ],
    }
    sources = [
        source
        for sensor in sensors
        for source in sources_by_sensor.get(sensor, [])
    ]
    if not sources:
        raise ValueError("Select MODIS, VIIRS, or both sensors for the comparison.")

    first_date = date.fromisoformat(first_day)
    if day_range < 1:
        raise ValueError("The comparison window must contain at least one day.")
    chunks: list[tuple[date, int]] = []
    chunk_start = first_date
    days_remaining = day_range
    while days_remaining:
        chunk_days = min(FIRMS_MAX_DAYS_PER_REQUEST, days_remaining)
        chunks.append((chunk_start, chunk_days))
        chunk_start += timedelta(days=chunk_days)
        days_remaining -= chunk_days

    frames: list[pd.DataFrame] = []
    failures: list[str] = []

    for source, sensor in sources:
        for chunk_start, chunk_days in chunks:
            url = (
                "https://firms.modaps.eosdis.nasa.gov/api/area/csv/"
                f"{quote(map_key, safe='')}/{source}/{area_coordinates}/"
                f"{chunk_days}/{chunk_start.isoformat()}"
            )
            try:
                with urlopen(url, timeout=30) as response:
                    payload = response.read()
                frame = normalize_csv(BytesIO(payload), sensor)
                frame["source"] = source
                frames.append(frame)
            except HTTPError as exc:
                response_text = exc.read().decode("utf-8", errors="replace").strip()
                response_text = response_text.replace(map_key, "[hidden key]")
                response_text = response_text.replace(
                    quote(map_key, safe=""), "[hidden key]"
                )
                detail = response_text[:240] or str(exc.reason)
                failures.append(
                    f"{source}: {chunk_start:%b %d} ({chunk_days} day(s)) returned "
                    f"HTTP {exc.code}: {detail}"
                )
            except Exception as exc:
                failures.append(
                    f"{source}: {chunk_start:%b %d} ({chunk_days} day(s)): {exc}"
                )

    if frames:
        return pd.concat(frames, ignore_index=True), failures
    raise RuntimeError("No historical FIRMS data could be loaded. " + "; ".join(failures))


@st.cache_data(ttl=600, show_spinner=False)
def load_multi_year_baseline(
    map_key: str,
    start_day: date,
    day_range: int,
    area_coordinates: str,
    years: int = 3,
    sensors: tuple[str, ...] = ("MODIS", "VIIRS"),
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    """Fetch same-date standard-processing records for several prior years."""
    all_source_groups = {
        "MODIS": {"MODIS_SP"},
        "VIIRS": {"VIIRS_NOAA20_SP", "VIIRS_NOAA21_SP"},
    }
    source_groups = {
        sensor: sources
        for sensor, sources in all_source_groups.items()
        if sensor in sensors
    }
    all_sources = set().union(*source_groups.values())
    frames: list[pd.DataFrame] = []
    year_status: list[dict[str, object]] = []

    for year_offset in range(1, years + 1):
        baseline_year = start_day.year - year_offset
        try:
            baseline_start = start_day.replace(year=baseline_year)
        except ValueError:
            # Match Feb 29 with Feb 28 in years that do not have a leap day.
            baseline_start = date(baseline_year, 2, 28)
        baseline_end = baseline_start + timedelta(days=day_range - 1)
        request_failed = False
        try:
            year_data, failures = load_historical_area(
                map_key,
                baseline_start.isoformat(),
                day_range,
                area_coordinates,
                sensors=sensors,
            )
        except Exception as exc:
            failures = [str(exc)]
            request_failed = True
            year_data = pd.DataFrame(
                columns=["latitude", "longitude", "date", "sensor", "source"]
            )

        failed_sources = all_sources if request_failed else {
            source
            for source in all_sources
            if any(message.startswith(f"{source}:") for message in failures)
            or any(source in message for message in failures if "could be loaded" in message)
        }
        successful_sources = all_sources - failed_sources
        for sensor, required_sources in source_groups.items():
            if required_sources.issubset(successful_sources):
                # A zero-row response is still successful and contributes a real zero.
                frames.append(
                    year_data[
                        year_data["source"].isin(required_sources)
                    ].assign(baseline_year=baseline_year)
                )

        year_status.append(
            {
                "year": baseline_year,
                "start": baseline_start,
                "end": baseline_end,
                "modis_complete": (
                    "MODIS" in source_groups
                    and source_groups["MODIS"].issubset(successful_sources)
                ),
                "viirs_complete": (
                    "VIIRS" in source_groups
                    and source_groups["VIIRS"].issubset(successful_sources)
                ),
                "errors": failures,
            }
        )

    if frames:
        baseline_data = pd.concat(frames, ignore_index=True)
    else:
        baseline_data = pd.DataFrame(
            columns=["latitude", "longitude", "date", "sensor", "source", "baseline_year"]
        )
    return baseline_data, year_status


def radius_control(value: float) -> float:
    """Render a radius input with press-and-hold, one-kilometer controls."""
    result = _radius_control_component(
        value=float(value),
        min_value=0.1,
        max_value=20015.0,
        default=float(value),
        key="firms_coordinate_radius_km_control",
    )
    try:
        radius = float(result)
    except (TypeError, ValueError):
        radius = float(value)
    radius = min(20015.0, max(0.1, radius))
    st.session_state["firms_coordinate_radius_km"] = radius
    return radius


with st.sidebar:
    st.header("Load FIRMS data")
    map_key = st.text_input(
        "Optional FIRMS map key",
        type="password",
        help="Used only to request recent NASA FIRMS data during this app session.",
    )
    map_key = map_key.strip()
    live_scope = st.selectbox(
        "Live data coverage",
        ["Coordinate search", "Whole globe"],
        help="Load a compact box around coordinates, or request the whole globe.",
    )
    if st.session_state.get("fireline_coordinate_inputs_version") != 2:
        st.session_state.pop("firms_coordinate_latitude", None)
        st.session_state.pop("firms_coordinate_longitude", None)
        st.session_state["fireline_coordinate_inputs_version"] = 2
    if live_scope == "Coordinate search":
        coordinate_latitude = st.number_input(
            "Center latitude",
            min_value=-90.0,
            max_value=90.0,
            value=None,
            step=0.1,
            format="%.4f",
            placeholder="Enter latitude",
            help="Enter any latitude from −90 to 90.",
            key="firms_coordinate_latitude",
        )
        coordinate_longitude = st.number_input(
            "Center longitude",
            min_value=-180.0,
            max_value=180.0,
            value=None,
            step=0.1,
            format="%.4f",
            placeholder="Enter longitude",
            help="Enter any longitude from −180 to 180.",
            key="firms_coordinate_longitude",
        )
        st.markdown("**Search radius (km)**")
        coordinate_radius_km = radius_control(
            float(st.session_state.get("firms_coordinate_radius_km", 100.0))
        )
        st.caption(
            "Type any value from 0.1 to 20,015 km, or hold +/− to adjust by 1 km. "
            "FIRMS searches a rectangular box extending this far from the center."
        )
    else:
        coordinate_latitude = st.session_state.get("firms_coordinate_latitude")
        coordinate_longitude = st.session_state.get("firms_coordinate_longitude")
        coordinate_radius_km = st.session_state.get("firms_coordinate_radius_km", 100)
    live_days = st.selectbox(
        "Days to load (1–5)",
        [1, 2, 3, 4, 5],
        index=4 if live_scope == "Coordinate search" else 0,
        key=f"firms_live_days_{live_scope.lower().replace(' ', '_')}",
    )
    coordinate_ready = coordinate_latitude is not None and coordinate_longitude is not None
    live_area_coordinates = "world" if live_scope == "Whole globe" else (
        coordinate_search_box(
            coordinate_latitude,
            coordinate_longitude,
            coordinate_radius_km,
        )
        if coordinate_ready
        else None
    )
    live_area_label = (
        "the whole globe"
        if live_scope == "Whole globe"
        else (
            f"{coordinate_radius_km:g} km around {coordinate_latitude:.4f}, {coordinate_longitude:.4f}"
            if coordinate_ready
            else "your coordinate area"
        )
    )
    load_live = st.button(
        f"Load {live_days} recent day(s) for {live_area_label}",
        disabled=not map_key or (live_scope == "Coordinate search" and not coordinate_ready),
    )
    st.caption("A free NASA FIRMS map key is needed for live data. CSV upload works without it.")
    if live_scope == "Coordinate search" and not coordinate_ready:
        st.caption("Enter both coordinates to enable the live-data button.")
    if live_scope == "Whole globe":
        st.caption(
            "Global requests can return tens of thousands of detections per day. "
            "They start at one day; choose Coordinate search for a smaller request."
        )
    modis_file = st.file_uploader("MODIS CSV", type=["csv"], key="modis")
    viirs_file = st.file_uploader("VIIRS CSV", type=["csv"], key="viirs")
    st.caption(
        "Export CSV files from NASA FIRMS Active Fire Data. You can upload either "
        "sensor or both; the records are combined for comparison."
    )

if load_live and map_key:
    with st.spinner(f"Loading {live_days} recent day(s) from NASA FIRMS for {live_area_label}…"):
        try:
            live_data, live_errors = load_live_area(
                map_key, live_area_coordinates, live_days
            )
            st.session_state["fire_calendar_live_data"] = live_data
            st.session_state["fire_calendar_live_errors"] = live_errors
            st.session_state["fire_calendar_live_scope"] = live_scope
            st.session_state["fire_calendar_live_days"] = live_days
            st.session_state["fire_calendar_live_area_coordinates"] = live_area_coordinates
            st.session_state["fire_calendar_area_of_interest"] = "All loaded detections"
        except Exception as exc:
            st.session_state["fire_calendar_live_data"] = None
            st.session_state["fire_calendar_live_errors"] = [str(exc)]

files = [(modis_file, "MODIS"), (viirs_file, "VIIRS")]
frames: list[pd.DataFrame] = []
errors: list[str] = []
for uploaded, sensor in files:
    if uploaded is not None:
        try:
            frames.append(normalize_csv(uploaded, sensor))
        except Exception as exc:  # Display a useful upload error instead of stopping the app.
            errors.append(f"{sensor}: {exc}")

live_data = st.session_state.get("fire_calendar_live_data")
errors.extend(st.session_state.get("fire_calendar_live_errors", []))
if live_data is not None:
    frames.append(live_data)

for error in errors:
    if "Invalid MAP_KEY" in error:
        st.error(
            "NASA FIRMS rejected this map key. Copy the complete key from NASA’s email, "
            "or check/resend it on the FIRMS map-key page. Keep the key private."
        )
        st.markdown(
            "[Open NASA FIRMS map-key page](https://firms.modaps.eosdis.nasa.gov/api/map_key/)"
        )
    else:
        st.error(error)

if not frames:
    st.info("Upload one or both NASA FIRMS CSV files to explore hotspot activity.")
    st.markdown(
        "**Suggested demo:** load a coordinate area or the whole globe, compare "
        "recent detections with an archived period, and explore where detections cluster."
    )
    st.link_button(
        "Open NASA FIRMS Active Fire Data",
        "https://firms.modaps.eosdis.nasa.gov/active_fire/",
    )
    st.stop()

data = pd.concat(frames, ignore_index=True)
if data.empty:
    st.warning("The uploaded files did not contain usable hotspot rows.")
    st.stop()

min_day = min(data["date"])
max_day = max(data["date"])
default_start = max(min_day, max_day - timedelta(days=29))
data_default_bbox = [
    float(coordinate) for coordinate in bounding_box_for_data(data).split(",")
]

st.subheader("Choose an area and time window")
left, sensor_col, right = st.columns([1, 1, 1.5])
with left:
    area_options = ["All loaded detections", "Custom bounding box"]
    if st.session_state.get("fire_calendar_area_of_interest") not in area_options:
        st.session_state["fire_calendar_area_of_interest"] = "All loaded detections"
    region = st.selectbox(
        "Area of interest",
        area_options,
        help=(
            "Choose all loaded points or filter them to a custom latitude/longitude rectangle."
        ),
        key="fire_calendar_area_of_interest",
    )
with sensor_col:
    sensor_mode = st.selectbox(
        "Sensor view",
        ["Both sensors", "MODIS only", "VIIRS only"],
        help="Filter every result below to one sensor, or view both together.",
    )
visible_sensors = {
    "Both sensors": ["MODIS", "VIIRS"],
    "MODIS only": ["MODIS"],
    "VIIRS only": ["VIIRS"],
}[sensor_mode]
loaded_sensors = set(data["sensor"].dropna().unique())
comparison_sensors = tuple(
    sensor for sensor in visible_sensors if sensor in loaded_sensors
)
with right:
    period = st.date_input(
        "Date range",
        value=(default_start, max_day),
        min_value=min_day,
        max_value=max_day,
    )

loaded_live_scope = st.session_state.get("fire_calendar_live_scope")
if loaded_live_scope:
    loaded_days = st.session_state.get("fire_calendar_live_days", 5)
    if loaded_live_scope not in {"Coordinate search", "Whole globe"}:
        loaded_scope_label = "previously loaded area"
    else:
        loaded_scope_label = loaded_live_scope.lower()
    st.caption(
        f"Live FIRMS data loaded for {loaded_scope_label} · "
        f"{loaded_days} day(s). Choose **All loaded detections** to view the full coverage."
    )
if region == "Custom bounding box":
    lat_col, lon_col = st.columns(2)
    with lat_col:
        south_limit = st.number_input(
            "South latitude",
            min_value=-90.0,
            max_value=90.0,
            value=data_default_bbox[1],
            step=0.1,
            format="%.2f",
            key="custom_south_latitude",
        )
        north_limit = st.number_input(
            "North latitude",
            min_value=-90.0,
            max_value=90.0,
            value=data_default_bbox[3],
            step=0.1,
            format="%.2f",
            key="custom_north_latitude",
        )
    with lon_col:
        west_limit = st.number_input(
            "West longitude",
            min_value=-180.0,
            max_value=180.0,
            value=data_default_bbox[0],
            step=0.1,
            format="%.2f",
            key="custom_west_longitude",
        )
        east_limit = st.number_input(
            "East longitude",
            min_value=-180.0,
            max_value=180.0,
            value=data_default_bbox[2],
            step=0.1,
            format="%.2f",
            key="custom_east_longitude",
        )
    st.caption(
        "A custom box filters points already loaded. To fetch a different area from NASA, "
        "choose **Coordinate search** in the sidebar and load again."
    )
    if south_limit >= north_limit or west_limit >= east_limit:
        st.error(
            "Set the south latitude below the north latitude, and the west longitude "
            "below the east longitude."
        )
        st.stop()

if isinstance(period, (tuple, list)):
    selected_days = [
        selected_day
        for selected_day in period
        if isinstance(selected_day, date)
    ]
    if len(selected_days) >= 2:
        start_day, end_day = selected_days[:2]
    elif selected_days:
        # Streamlit returns a one-date tuple while the user is choosing a range end.
        start_day = end_day = selected_days[0]
    else:
        start_day = end_day = max_day
elif isinstance(period, date):
    start_day = end_day = period
else:
    # Fall back safely if the date widget is temporarily empty.
    start_day = end_day = max_day

if region == "Custom bounding box":
    comparison_area = (
        f"{west_limit:.4f},{south_limit:.4f},{east_limit:.4f},{north_limit:.4f}"
    )
elif loaded_live_scope == "Whole globe":
    comparison_area = "world"
elif loaded_live_scope == "Coordinate search":
    comparison_area = st.session_state.get(
        "fire_calendar_live_area_coordinates",
        bounding_box_for_data(data),
    )
else:
    comparison_area = bounding_box_for_data(data)

view = data[(data["date"] >= start_day) & (data["date"] <= end_day)].copy()
if region == "Custom bounding box":
    view = view[
        view["latitude"].between(south_limit, north_limit)
        & view["longitude"].between(west_limit, east_limit)
    ]
map_center_view = view
view = view[view["sensor"].isin(visible_sensors)]

if view.empty:
    st.warning(
        "No detections match that sensor, area, and date range. Try both sensors, "
        "all loaded detections, or another range."
    )
    st.stop()

st.subheader("Compare with the same dates last year")
comparison_days = (end_day - start_day).days + 1
previous_year = start_day.year - 1
try:
    previous_start = start_day.replace(year=previous_year)
except ValueError:
    previous_start = date(previous_year, 2, 28)
previous_end = previous_start + timedelta(days=comparison_days - 1)

compare_disabled = not map_key
st.caption(
    f"Compare {start_day:%b %d, %Y}–{end_day:%b %d, %Y} with "
    f"{previous_start:%b %d, %Y}–{previous_end:%b %d, %Y}. "
    "The previous-year request uses NASA FIRMS standard-processing data and the "
    f"same selected area. Requested sensor(s): {', '.join(comparison_sensors)}."
)
if comparison_days > FIRMS_MAX_DAYS_PER_REQUEST:
    st.caption(
        f"This {comparison_days}-day window will be fetched in chunks of up to "
        f"{FIRMS_MAX_DAYS_PER_REQUEST} days for the available selected sensor(s)."
    )
if comparison_area == "world":
    st.caption(
        "A global historical comparison can take longer because FIRMS returns many "
        "records. Choose a custom bounding box for a smaller area."
    )
if not map_key:
    st.info("Enter your FIRMS map key in the sidebar to load the historical comparison.")

if st.button(
    f"Load {comparison_days} comparison days",
    disabled=compare_disabled,
    key="load_historical_comparison",
):
    with st.spinner("Loading the matching dates from NASA FIRMS…"):
        try:
            historical_data, historical_errors = load_historical_area(
                map_key,
                previous_start.isoformat(),
                comparison_days,
                comparison_area,
                sensors=comparison_sensors,
            )
            st.session_state["fire_calendar_historical_data"] = historical_data
            st.session_state["fire_calendar_historical_errors"] = historical_errors
            st.session_state["fire_calendar_historical_dates"] = (
                start_day.isoformat(),
                end_day.isoformat(),
            )
            st.session_state["fire_calendar_historical_area"] = comparison_area
        except Exception as exc:
            st.session_state["fire_calendar_historical_data"] = None
            st.session_state["fire_calendar_historical_errors"] = [str(exc)]
            st.session_state["fire_calendar_historical_dates"] = (
                start_day.isoformat(),
                end_day.isoformat(),
            )
            st.session_state["fire_calendar_historical_area"] = comparison_area

active_comparison = st.session_state.get("fire_calendar_historical_dates") == (
    start_day.isoformat(),
    end_day.isoformat(),
) and st.session_state.get("fire_calendar_historical_area") == comparison_area
historical_data = (
    st.session_state.get("fire_calendar_historical_data") if active_comparison else None
)
historical_errors = (
    st.session_state.get("fire_calendar_historical_errors", []) if active_comparison else []
)
for error in historical_errors:
    st.warning(error)

if active_comparison and historical_data is None:
    st.error(
        "NASA FIRMS could not load the comparison. If the message says Invalid MAP_KEY, "
        "check the key on NASA’s map-key page."
    )
elif historical_data is not None:
    if region == "Custom bounding box":
        historical_view = historical_data[
            historical_data["latitude"].between(south_limit, north_limit)
            & historical_data["longitude"].between(west_limit, east_limit)
        ].copy()
    else:
        historical_view = historical_data.copy()
    historical_view = historical_view[
        historical_view["sensor"].isin(comparison_sensors)
    ]

    if historical_view.empty:
        st.info(
            "NASA returned no detections for the selected sensor and matching dates "
            "last year in this area."
        )
    else:
        current_counts = view.groupby("sensor").size().to_dict()
        past_counts = historical_view.groupby("sensor").size().to_dict()
        comparison_rows = []
        for sensor in comparison_sensors:
            current_count = int(current_counts.get(sensor, 0))
            past_count = int(past_counts.get(sensor, 0))
            change = (
                "— (no detections last year)"
                if past_count == 0
                else f"{(current_count / past_count - 1) * 100:+.1f}%"
            )
            comparison_rows.append(
                {
                    "Sensor": sensor,
                    "Selected dates": current_count,
                    "Same dates last year": past_count,
                    "Change in detections": change,
                }
            )

        summary = pd.DataFrame(comparison_rows)
        st.dataframe(summary, hide_index=True, width="stretch")

        chart_data = pd.DataFrame(
            [
                {"Sensor": sensor, "Period": "Selected dates", "Detections": int(current_counts.get(sensor, 0))}
                for sensor in comparison_sensors
            ]
            + [
                {"Sensor": sensor, "Period": "Same dates last year", "Detections": int(past_counts.get(sensor, 0))}
                for sensor in comparison_sensors
            ]
        )
        comparison_chart = px.bar(
            chart_data,
            x="Sensor",
            y="Detections",
            color="Period",
            barmode="group",
            color_discrete_map={
                "Selected dates": "#f59e0b",
                "Same dates last year": "#64748b",
            },
            labels={"Sensor": "Sensor", "Detections": "Satellite detections"},
        )
        comparison_chart.update_layout(height=360, margin={"l": 0, "r": 0, "t": 20, "b": 0})
        st.plotly_chart(comparison_chart, width="stretch")
        st.caption(
            "Compare each sensor only with itself. MODIS and VIIRS have different resolutions, "
            "and a detection is not a count of separate fires. Recent data is near real-time; "
            "the older comparison uses standard-processing records."
        )

st.subheader("Compare with up to 5 years of history")
baseline_year_count = 5
baseline_disabled = not map_key
st.caption(
    "This compares the selected dates with the average for the same date window in the "
    "five preceding years when data is available. MODIS and VIIRS are each compared "
    "with their own history; missing sensor-years are excluded and listed below."
)
if not map_key:
    st.info("Enter your FIRMS map key in the sidebar to load the multi-year baseline.")
if comparison_area == "world":
    st.caption(
        "A global multi-year request can take longer and return many records. "
        "A custom bounding box can make it faster."
    )

if st.button(
    "Load up-to-5-year baseline",
    disabled=baseline_disabled,
    key="load_multi_year_baseline",
):
    with st.spinner("Loading matching dates from NASA FIRMS for up to five years…"):
        baseline_data, baseline_status = load_multi_year_baseline(
            map_key,
            start_day,
            comparison_days,
            comparison_area,
            baseline_year_count,
            sensors=comparison_sensors,
        )
        st.session_state["fire_calendar_baseline_data"] = baseline_data
        st.session_state["fire_calendar_baseline_status"] = baseline_status
        st.session_state["fire_calendar_baseline_dates"] = (
            start_day.isoformat(),
            end_day.isoformat(),
        )
        st.session_state["fire_calendar_baseline_area"] = comparison_area

active_baseline = st.session_state.get("fire_calendar_baseline_dates") == (
    start_day.isoformat(),
    end_day.isoformat(),
) and st.session_state.get("fire_calendar_baseline_area") == comparison_area
baseline_data = (
    st.session_state.get("fire_calendar_baseline_data") if active_baseline else None
)
baseline_status = (
    st.session_state.get("fire_calendar_baseline_status", []) if active_baseline else []
)
baseline_view = None
baseline_complete_years: dict[str, list[int]] = {sensor: [] for sensor in comparison_sensors}
baseline_daily_rows: list[dict[str, object]] = []

if active_baseline and baseline_data is not None:
    if region == "Custom bounding box":
        baseline_view = baseline_data[
            baseline_data["latitude"].between(south_limit, north_limit)
            & baseline_data["longitude"].between(west_limit, east_limit)
        ].copy()
    else:
        baseline_view = baseline_data.copy()
    baseline_view = baseline_view[baseline_view["sensor"].isin(comparison_sensors)]

    incomplete = []
    for item in baseline_status:
        for sensor, key in (("MODIS", "modis_complete"), ("VIIRS", "viirs_complete")):
            if sensor in comparison_sensors and item.get(key):
                baseline_complete_years[sensor].append(int(item["year"]))
        missing = []
        if "MODIS" in comparison_sensors and not item.get("modis_complete"):
            missing.append("MODIS")
        if "VIIRS" in comparison_sensors and not item.get("viirs_complete"):
            missing.append("VIIRS")
        if missing:
            incomplete.append(f"{item['year']}: {', '.join(missing)} unavailable")

    if incomplete:
        st.warning(
            "Some sensor-years could not be included in the average: "
            + "; ".join(incomplete)
            + ". The table uses only complete years for each sensor."
        )
        failure_details = [
            f"{item['year']}: {message}"
            for item in baseline_status
            for message in item.get("errors", [])
        ]
        if failure_details:
            with st.expander("Details about unavailable baseline data"):
                for detail in failure_details:
                    st.write(detail)

    current_counts = view.groupby("sensor").size().to_dict()
    baseline_summary_rows = []
    baseline_year_rows = []
    baseline_average_by_sensor: dict[str, float] = {}
    for sensor in comparison_sensors:
        years_for_sensor = baseline_complete_years[sensor]
        year_counts = []
        for baseline_year in years_for_sensor:
            count = int(
                len(
                    baseline_view[
                        (baseline_view["sensor"] == sensor)
                        & (baseline_view["baseline_year"] == baseline_year)
                    ]
                )
            )
            year_counts.append(count)
            baseline_year_rows.append(
                {"Sensor": sensor, "Year": baseline_year, "Detections": count}
            )

        current_count = int(current_counts.get(sensor, 0))
        if year_counts:
            baseline_average = sum(year_counts) / len(year_counts)
            baseline_average_by_sensor[sensor] = baseline_average
            change = (
                f"{(current_count / baseline_average - 1) * 100:+.1f}%"
                if baseline_average > 0
                else ("0.0%" if current_count == 0 else "Above a zero baseline")
            )
            average_label = f"{baseline_average:,.1f}"
            years_label = ", ".join(str(year) for year in years_for_sensor)
        else:
            change = "Unavailable"
            average_label = "Unavailable"
            years_label = "None"
        baseline_summary_rows.append(
            {
                "Sensor": sensor,
                "Selected dates": current_count,
                "Average (up to 5 years)": average_label,
                "Years included": years_label,
                "Change vs average": change,
            }
        )

    if any(baseline_complete_years.values()):
        st.dataframe(pd.DataFrame(baseline_summary_rows), hide_index=True, width="stretch")
        if baseline_year_rows:
            with st.expander("See each year's baseline count"):
                st.dataframe(
                    pd.DataFrame(baseline_year_rows),
                    hide_index=True,
                    width="stretch",
                )

        baseline_chart_rows = []
        for sensor in comparison_sensors:
            baseline_average = baseline_average_by_sensor.get(sensor)
            if baseline_average is not None:
                baseline_chart_rows.extend(
                    [
                        {
                            "Sensor": sensor,
                            "Period": "Selected dates",
                            "Detections": int(current_counts.get(sensor, 0)),
                        },
                        {
                            "Sensor": sensor,
                            "Period": "Available-year average",
                            "Detections": baseline_average,
                        },
                    ]
                )
        baseline_chart = px.bar(
            pd.DataFrame(baseline_chart_rows),
            x="Sensor",
            y="Detections",
            color="Period",
            barmode="group",
            color_discrete_map={
                "Selected dates": "#f59e0b",
                "Available-year average": "#64748b",
            },
            labels={"Sensor": "Sensor", "Detections": "Satellite detections"},
        )
        baseline_chart.update_layout(
            height=340,
            margin={"l": 0, "r": 0, "t": 20, "b": 0},
        )
        st.plotly_chart(baseline_chart, width="stretch")
        st.caption(
            "A higher value means more satellite detections than the sensor's own "
            "average across the sensor's available years (up to five). It does not prove that there are more "
            "separate fires. Current detections are near real-time; baseline years use "
            "standard-processing data."
        )
    else:
        st.info("No complete sensor-years were available for the selected date window and area.")

    if baseline_complete_years and baseline_status:
        selected_daily_counts = view.groupby(["date", "sensor"]).size().to_dict()
        baseline_daily_counts = baseline_view.groupby(
            ["baseline_year", "date", "sensor"]
        ).size().to_dict()
        for offset in range(comparison_days):
            selected_date = start_day + timedelta(days=offset)
            for sensor in comparison_sensors:
                counts = []
                for item in baseline_status:
                    year = int(item["year"])
                    complete_key = "modis_complete" if sensor == "MODIS" else "viirs_complete"
                    if not item.get(complete_key):
                        continue
                    baseline_date = item["start"] + timedelta(days=offset)
                    counts.append(
                        int(
                            baseline_daily_counts.get(
                                (year, baseline_date, sensor), 0
                            )
                        )
                    )
                if counts:
                    baseline_daily_rows.extend(
                        [
                            {
                                "Date": selected_date.strftime("%b %d"),
                                "Sensor": sensor,
                                "Period": "Selected dates",
                                "Detections": int(
                                    selected_daily_counts.get((selected_date, sensor), 0)
                                ),
                            },
                            {
                                "Date": selected_date.strftime("%b %d"),
                                "Sensor": sensor,
                                "Period": "Available-year daily average",
                                "Detections": sum(counts) / len(counts),
                            },
                        ]
                    )

sensor_counts = view.groupby("sensor").size().to_dict()
metric_cols = st.columns(1 + len(visible_sensors))
metric_cols[0].metric("Detections in view", f"{len(view):,}")
for metric_col, sensor in zip(metric_cols[1:], visible_sensors):
    metric_col.metric(sensor, f"{sensor_counts.get(sensor, 0):,}")

map_col, calendar_col = st.columns([1.15, 1])
with map_col:
    st.subheader("Explore detections on the globe")
    # Keep the map's sensor traces and point data stable when the sensor filter
    # changes; only toggle trace visibility so Plotly can preserve the camera.
    map_sample = sample_map_points(map_center_view, MAP_POINT_LIMIT)
    visible_map_sample = map_sample[map_sample["sensor"].isin(visible_sensors)]
    globe_center_lon = float(map_center_view["longitude"].median())
    globe_center_lat = float(map_center_view["latitude"].median())
    globe_revision = (
        f"{region}|{start_day}|{end_day}|{comparison_area}|"
        f"{globe_center_lon:.3f}|{globe_center_lat:.3f}"
    )
    saved_globe_camera = st.session_state.get(GLOBE_CAMERA_SESSION_KEY)
    if (
        isinstance(saved_globe_camera, dict)
        and saved_globe_camera.get("version") == 2
        and saved_globe_camera.get("revision") == globe_revision
    ):
        saved_rotation = saved_globe_camera.get("rotation", {})
        globe_center_lon = saved_rotation.get("lon", -globe_center_lon)
        globe_center_lat = saved_rotation.get("lat", -globe_center_lat)
    map_chart = px.scatter_geo(
        map_sample,
        lat="latitude",
        lon="longitude",
        color="sensor",
        symbol="sensor",
        symbol_map={"MODIS": "diamond", "VIIRS": "circle"},
        hover_data=["date"],
        projection="orthographic",
        color_discrete_map={"MODIS": "#f59e0b", "VIIRS": "#38bdf8"},
    )
    if {"MODIS", "VIIRS"}.issubset({trace.name for trace in map_chart.data}):
        for trace in map_chart.data:
            trace.legendrank = 1 if trace.name == "MODIS" else 2
        map_chart.data = tuple(
            trace
            for sensor_name in ("VIIRS", "MODIS")
            for trace in map_chart.data
            if trace.name == sensor_name
        )
    for trace in map_chart.data:
        trace.visible = trace.name in visible_sensors
    map_chart.update_traces(marker={"size": 4, "opacity": 0.82})
    projection_rotation = {"lon": globe_center_lon, "lat": globe_center_lat}
    if (
        isinstance(saved_globe_camera, dict)
        and saved_globe_camera.get("version") == 2
        and saved_globe_camera.get("revision") == globe_revision
    ):
        saved_rotation = saved_globe_camera.get("rotation", {})
        if saved_rotation.get("roll") is not None:
            projection_rotation["roll"] = saved_rotation["roll"]
    projection_scale = (
        saved_globe_camera.get("scale")
        if isinstance(saved_globe_camera, dict)
        and saved_globe_camera.get("version") == 2
        and saved_globe_camera.get("revision") == globe_revision
        else None
    )
    map_chart.update_geos(
        projection_type="orthographic",
        projection_rotation=projection_rotation,
        projection_scale=projection_scale,
        uirevision=globe_revision,
        showland=True,
        landcolor="#1c493f",
        showocean=True,
        oceancolor="#102841",
        showcountries=True,
        countrycolor="#638074",
        showcoastlines=True,
        coastlinecolor="#82a494",
        showlakes=True,
        lakecolor="#102841",
        bgcolor="#071521",
    )
    map_chart.update_layout(
        height=540,
        margin={"l": 0, "r": 0, "t": 10, "b": 0},
        # Keep the current globe orientation when only the sensor filter changes.
        # Area or date changes still reset the camera to that selection's center.
        uirevision=globe_revision,
    )
    st.plotly_chart(map_chart, width="stretch", key="fireline_atlas_globe")
    capture_globe_camera(globe_revision)
    if len(map_center_view) > MAP_POINT_LIMIT:
        sample_counts = visible_map_sample.groupby("sensor").size().to_dict()
        full_counts = view.groupby("sensor").size().to_dict()
        sensor_sample_summary = " · ".join(
            f"{sensor}: {sample_counts.get(sensor, 0):,} of {full_counts.get(sensor, 0):,}"
            for sensor in visible_sensors
        )
        st.caption(
            f"Balanced globe sample: {len(visible_map_sample):,} visible markers "
            f"({sensor_sample_summary}). "
            "All counts, the calendar, and downloads use the full data."
        )
    st.caption("Drag to rotate the globe and scroll to zoom. Hotspots are plotted over the Earth.")

with calendar_col:
    st.subheader("Daily hotspot calendar")
    calendar_dates = [
        start_day + timedelta(days=offset)
        for offset in range((end_day - start_day).days + 1)
    ]
    sensor_order = visible_sensors
    calendar_counts = (
        view.groupby(["date", "sensor"])
        .size()
        .unstack(fill_value=0)
        .reindex(index=calendar_dates, columns=sensor_order, fill_value=0)
    )
    date_labels = [day.strftime("%b %d") for day in calendar_dates]
    tick_step = max(1, len(date_labels) // 10)
    calendar_chart = px.imshow(
        calendar_counts.T,
        x=date_labels,
        y=sensor_order,
        text_auto=True,
        aspect="auto",
        color_continuous_scale="Cividis",
        labels={"x": "Date", "y": "Sensor", "color": "Detections"},
    )
    calendar_chart.update_traces(
        hovertemplate="%{y}<br>%{x}: %{z} detections<extra></extra>"
    )
    calendar_chart.update_xaxes(
        tickmode="array",
        tickvals=date_labels[::tick_step],
        ticktext=date_labels[::tick_step],
        tickangle=-35,
    )
    calendar_chart.update_layout(
        height=300,
        margin={"l": 0, "r": 0, "t": 10, "b": 40},
        coloraxis_colorbar={"title": "Detections"},
    )
    st.plotly_chart(calendar_chart, width="stretch")

    st.caption(
        "Each cell prints that sensor’s detections for the date. The color-vision-friendly "
        "Cividis ramp adds a visual cue, but color is not the only way to read the values. "
        "Compare MODIS with MODIS and VIIRS with VIIRS."
    )
    with st.expander("See daily counts in a table"):
        calendar_table = calendar_counts.rename_axis("Date").reset_index()
        st.dataframe(calendar_table, hide_index=True, width="stretch")

if historical_data is not None and not historical_view.empty:
    st.subheader("Daily comparison by sensor")
    st.caption(
        "The lines match each selected date with the same calendar date one year earlier. "
        "Compare MODIS with MODIS and VIIRS with VIIRS; the sensors have different "
        "pixel sizes and detection behavior."
    )
    selected_dates = [
        start_day + timedelta(days=offset)
        for offset in range(comparison_days)
    ]
    prior_dates = [
        previous_start + timedelta(days=offset)
        for offset in range(comparison_days)
    ]
    selected_daily_counts = view.groupby(["date", "sensor"]).size().to_dict()
    prior_daily_counts = historical_view.groupby(["date", "sensor"]).size().to_dict()
    daily_comparison_rows = []
    daily_table_rows = []
    for selected_date, prior_date in zip(selected_dates, prior_dates):
        row = {"Date": selected_date.strftime("%b %d")}
        for sensor in sensor_order:
            selected_count = int(selected_daily_counts.get((selected_date, sensor), 0))
            prior_count = int(prior_daily_counts.get((prior_date, sensor), 0))
            row[f"{sensor} · selected"] = selected_count
            row[f"{sensor} · last year"] = prior_count
            daily_comparison_rows.extend(
                [
                    {
                        "Date": selected_date.strftime("%b %d"),
                        "Sensor": sensor,
                        "Period": "Selected dates",
                        "Detections": selected_count,
                    },
                    {
                        "Date": selected_date.strftime("%b %d"),
                        "Sensor": sensor,
                        "Period": "Same dates last year",
                        "Detections": prior_count,
                    },
                ]
            )
        daily_table_rows.append(row)

    daily_comparison_table = pd.DataFrame(daily_table_rows)
    st.dataframe(daily_comparison_table, hide_index=True, width="stretch")
    daily_comparison_chart = px.line(
        pd.DataFrame(daily_comparison_rows),
        x="Date",
        y="Detections",
        color="Period",
        facet_col="Sensor",
        markers=True,
        category_orders={"Sensor": sensor_order},
        color_discrete_map={
            "Selected dates": "#f59e0b",
            "Same dates last year": "#64748b",
        },
        labels={"Date": "Day", "Detections": "Satellite detections"},
    )
    daily_comparison_chart.update_layout(
        height=380,
        margin={"l": 0, "r": 0, "t": 35, "b": 0},
        legend_title_text="",
    )
    daily_comparison_chart.update_yaxes(matches=None)
    st.plotly_chart(daily_comparison_chart, width="stretch")

if baseline_daily_rows:
    st.subheader("Daily counts vs available-year average")
    st.caption(
        "Each daily average uses only years where that sensor's full FIRMS product set "
        "was available. Zero-detection days count as zero."
    )
    baseline_daily_frame = pd.DataFrame(baseline_daily_rows)
    baseline_daily_table = baseline_daily_frame.pivot(
        index="Date", columns=["Sensor", "Period"], values="Detections"
    ).reset_index()
    baseline_daily_table.columns = [
        "Date" if column == ("Date", "") else " · ".join(str(value) for value in column if value)
        if isinstance(column, tuple)
        else str(column)
        for column in baseline_daily_table.columns
    ]
    st.dataframe(baseline_daily_table, hide_index=True, width="stretch")
    baseline_daily_chart = px.line(
        baseline_daily_frame,
        x="Date",
        y="Detections",
        color="Period",
        facet_col="Sensor",
        markers=True,
        category_orders={"Sensor": sensor_order},
        color_discrete_map={
            "Selected dates": "#f59e0b",
            "Available-year daily average": "#64748b",
        },
        labels={"Date": "Day", "Detections": "Satellite detections"},
    )
    baseline_daily_chart.update_layout(
        height=380,
        margin={"l": 0, "r": 0, "t": 35, "b": 0},
        legend_title_text="",
    )
    baseline_daily_chart.update_yaxes(matches=None)
    st.plotly_chart(baseline_daily_chart, width="stretch")

st.divider()
st.markdown(
    "**Data credit:** Active-fire and thermal-anomaly detections from NASA’s "
    "[Fire Information for Resource Management System (FIRMS)]"
    "(https://firms.modaps.eosdis.nasa.gov/), using MODIS and VIIRS products. "
    "Recent live results are near real-time; historical comparisons use standard-processing data. "
    "[NASA FIRMS data and API information]"
    "(https://firms.modaps.eosdis.nasa.gov/active_fire/) · "
    "[NASA FIRMS map and data notes]"
    "(https://firms.modaps.eosdis.nasa.gov/map/)"
)
with st.expander("View the harmonized records"):
    if st.checkbox("Show all filtered records", key="show_all_filtered_records"):
        st.dataframe(view.sort_values("date", ascending=False), width="stretch")
    if st.button("Prepare filtered CSV", key="prepare_filtered_csv"):
        csv = view.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download filtered CSV",
            csv,
            "fire-calendar.csv",
            "text/csv",
            on_click="ignore",
        )
