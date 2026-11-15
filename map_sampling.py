"""Helpers for building responsive, sensor-balanced globe samples."""

import pandas as pd


def sample_map_points(frame: pd.DataFrame, limit: int) -> pd.DataFrame:
    """Return at most ``limit`` rows, sharing slots evenly across sensors.

    Any unused slots from a small sensor group are redistributed to the other
    groups so the globe still uses its full marker budget when possible.
    """
    if limit <= 0 or frame.empty:
        return frame.iloc[0:0].copy()
    if len(frame) <= limit:
        return frame.copy()

    working = frame.reset_index(drop=True)
    groups = list(working.groupby("sensor", sort=False))
    if not groups:
        return working.sample(n=min(limit, len(working)), random_state=7)

    base_quota, extra_slots = divmod(limit, len(groups))
    selected = []
    for group_number, (_, sensor_rows) in enumerate(groups):
        quota = base_quota + (1 if group_number < extra_slots else 0)
        take = min(len(sensor_rows), quota)
        if take:
            selected.append(sensor_rows.sample(n=take, random_state=7))

    sample = pd.concat(selected, axis=0) if selected else working.iloc[0:0]
    remaining_slots = limit - len(sample)
    if remaining_slots:
        remainder = working.drop(index=sample.index)
        if not remainder.empty:
            sample = pd.concat(
                [sample, remainder.sample(n=min(remaining_slots, len(remainder)), random_state=7)],
                axis=0,
            )

    return sample.reset_index(drop=True)
