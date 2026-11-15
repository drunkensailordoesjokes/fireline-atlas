# Fireline Atlas

A NASA Space Apps Challenge 2026 prototype for **Harmonization of MODIS and VIIRS Hot Spots**. Fireline Atlas maps satellite active-fire and thermal-anomaly detections and presents them in a sensor-aware daily calendar.

## What it does

- Loads recent MODIS and VIIRS detections for a coordinate search area or the whole globe using the NASA FIRMS API, or accepts FIRMS CSV uploads.
- Filters the map and calendar by date, sensor, all loaded data, or a custom latitude/longitude box.
- Shows detections on an interactive, rotatable 3D globe.
- Shows daily detections in a calendar heatmap, keeping MODIS and VIIRS in separate rows.
- Uses a color-vision-friendly calendar ramp and provides the daily values as a table too.
- Compares daily counts for a selected window with the same dates one year earlier, separately for each sensor.
- Compares selected counts and daily patterns with an average from up to five prior years for the matching dates, separately for each sensor, and shows which years were included.
- Lets you inspect and download the filtered records.

## Run locally

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

Run the sample-data UI smoke test with:

```bash
python -m unittest discover -s tests -v
```

For live data, request a free [NASA FIRMS map key](https://firms.modaps.eosdis.nasa.gov/api/map_key/) and enter it in the password-style field in the app. The key is used for requests during the current app session and is not written into project files. Choose a center latitude/longitude and search radius, or choose the whole globe under **Live data coverage**. Coordinate searches default to five days; the whole-globe option defaults to one day because global requests can return many detections. You can choose between one and five days for either area. The app requests MODIS, VIIRS NOAA-20, and VIIRS NOAA-21.

For uploaded data, use CSV files from [NASA FIRMS Active Fire Data](https://firms.modaps.eosdis.nasa.gov/active_fire/). The app expects latitude, longitude, and acquisition date columns; it also reads common confidence, time, and brightness fields when present.

The coordinate search turns a center point and radius into a rectangular request area, because the FIRMS Area API accepts bounding boxes. **Custom bounding box** filters points already loaded; choose **Coordinate search** in the sidebar and load again to fetch live data for a new area. For a global live map, load **Whole globe** and choose **All loaded detections**; or select a custom box to focus on one part of the world. Historical comparisons query the same selected area, with global comparisons potentially taking longer and returning many records.

## How to read the results

A hotspot is a satellite detection of unusual heat in a pixel at the time of an overpass. It is not a fire perimeter, a count of separate fires, or confirmation that a fire remains active. Detections may represent wildfires, agricultural burning, or other hot sources. Clouds, smoke, tree cover, and satellite overpass timing can affect what is detected.

NASA FIRMS describes MODIS detections at about 1 km pixels and VIIRS at about 375 m pixels. VIIRS can detect some smaller or less intense fires that MODIS may miss. For that reason, the calendar keeps sensor counts separate; compare MODIS with MODIS and VIIRS with VIIRS. The app is for exploring patterns, not for emergency response or fire-spread prediction.

Recent live results use near-real-time products. The prior-year and up-to-five-year comparisons use standard-processing products, so processing status is not identical across the periods. The multi-year average includes only years where the full product set for that sensor was available; years that could not be loaded are excluded rather than treated as zero. A higher detection count is a change in satellite observations, not proof of a change in the number of separate fires.

## Data credit

Active-fire and thermal-anomaly detections are from NASA's **Fire Information for Resource Management System (FIRMS)**, using MODIS and VIIRS products. Data is retrieved from NASA FIRMS or supplied by the user as a FIRMS CSV.

- [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/)
- [NASA FIRMS Active Fire Data and API information](https://firms.modaps.eosdis.nasa.gov/active_fire/)
- [NASA FIRMS map and data notes](https://firms.modaps.eosdis.nasa.gov/map/)
