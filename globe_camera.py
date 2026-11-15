"""Capture the Plotly globe's interactive camera across Streamlit reruns."""

import streamlit as st


GLOBE_CAMERA_COMPONENT_KEY = "fireline_atlas_globe_camera_capture_v5"
GLOBE_CAMERA_SESSION_KEY = "fireline_atlas_globe_camera"


def _save_globe_camera() -> None:
    component_state = st.session_state.get(GLOBE_CAMERA_COMPONENT_KEY)
    camera = getattr(component_state, "camera", None)
    if not isinstance(camera, dict) or camera.get("version") != 2:
        return

    previous = st.session_state.get(GLOBE_CAMERA_SESSION_KEY)
    if (
        isinstance(previous, dict)
        and previous.get("version") == 2
        and previous.get("revision") == camera.get("revision")
    ):
        merged = {**previous, **camera}
        if isinstance(camera.get("rotation"), dict):
            merged["rotation"] = {
                **previous.get("rotation", {}),
                **camera["rotation"],
            }
        if camera.get("scale") is None:
            merged["scale"] = previous.get("scale")
        if merged != previous:
            st.session_state[GLOBE_CAMERA_SESSION_KEY] = merged
    else:
        st.session_state[GLOBE_CAMERA_SESSION_KEY] = camera


_camera_capture = st.components.v2.component(
    name="fireline_atlas_globe_camera_capture_v5",
    html="<span aria-hidden='true'></span>",
    js="""
    export default function(component) {
      const { data, parentElement } = component;
      const selector = data?.selector;
      const revision = data?.revision;
      const savedCamera = data?.camera;
      const storageKey = `fireline_atlas_globe_camera_v4:${encodeURIComponent(revision || "")}`;
      const attachedGraphs = new WeakMap();
      let pendingRestore = null;
      let applyingCamera = false;
      let lastUserCameraChange = 0;

      const readStoredCamera = () => {
        try {
          const stored = JSON.parse(window.localStorage.getItem(storageKey) || "null");
          return stored?.version === 2 && stored?.revision === revision ? stored : null;
        } catch (_error) {
          return null;
        }
      };

      const saveStoredCamera = (camera) => {
        const previous = readStoredCamera() || savedCamera || {};
        const merged = { ...previous, ...camera, version: 2, revision };
        if (camera.rotation) {
          merged.rotation = { ...(previous.rotation || {}), ...camera.rotation };
        }
        try {
          window.localStorage.setItem(storageKey, JSON.stringify(merged));
        } catch (_error) {
          // The in-memory Streamlit state still works when browser storage is unavailable.
        }
      };

      if (
        savedCamera?.version === 2 && savedCamera.revision === revision &&
        !readStoredCamera()
      ) {
        saveStoredCamera(savedCamera);
      }

      if (parentElement instanceof HTMLElement) {
        parentElement.style.display = "none";
      }

      const attachToGlobe = () => {
        const graph = document.querySelector(selector);
        if (!graph || typeof graph.on !== "function" || attachedGraphs.has(graph)) {
          return;
        }

        const applySavedCamera = () => {
          const cameraToApply = readStoredCamera() || savedCamera;
          if (
            !cameraToApply || cameraToApply.version !== 2 ||
            cameraToApply.revision !== revision || typeof window.Plotly?.relayout !== "function"
          ) return;

          const update = {};
          const rotation = cameraToApply.rotation || {};
          const projection = graph._fullLayout?.geo?.projection;
          const numberOrNull = (value) =>
            value !== null && value !== undefined && Number.isFinite(Number(value))
              ? Number(value)
              : null;
          const isDifferent = (current, desired) =>
            desired !== null &&
            (current === null || Math.abs(current - desired) > 0.01);
          const currentRotation = projection?.rotation || {};
          const current = {
            lon: numberOrNull(currentRotation.lon),
            lat: numberOrNull(currentRotation.lat),
            roll: numberOrNull(currentRotation.roll),
            scale: numberOrNull(projection?.scale),
          };
          const desired = {
            lon: numberOrNull(rotation.lon),
            lat: numberOrNull(rotation.lat),
            roll: numberOrNull(rotation.roll),
            scale: numberOrNull(cameraToApply.scale),
          };
          if (isDifferent(current.lon, desired.lon)) {
            update["geo.projection.rotation.lon"] = desired.lon;
          }
          if (isDifferent(current.lat, desired.lat)) {
            update["geo.projection.rotation.lat"] = desired.lat;
          }
          if (isDifferent(current.roll, desired.roll)) {
            update["geo.projection.rotation.roll"] = desired.roll;
          }
          if (isDifferent(current.scale, desired.scale)) {
            update["geo.projection.scale"] = desired.scale;
          }
          if (Object.keys(update).length === 0) return;

          applyingCamera = true;
          graph.__firelineAtlasApplyingCamera = true;
          Promise.resolve(window.Plotly.relayout(graph, update)).finally(() => {
            window.setTimeout(() => {
              applyingCamera = false;
              graph.__firelineAtlasApplyingCamera = false;
            }, 0);
          });
        };

        const handleRelayout = (changes = {}) => {
          if (applyingCamera || graph.__firelineAtlasApplyingCamera) return;
          const changedKeys = Object.keys(changes);
          const rotationChanged = changedKeys.some((key) =>
            key.startsWith("geo.projection.rotation") ||
            key === "geo.projection.rotation"
          );
          const scaleChanged = changedKeys.includes("geo.projection.scale");
          if (!rotationChanged && !scaleChanged) return;
          lastUserCameraChange = Date.now();

          const projection = graph._fullLayout?.geo?.projection;
          const eventRotation = changes["geo.projection.rotation"] || {};
          const numberOrNull = (value) =>
            value !== null && value !== undefined && Number.isFinite(Number(value))
              ? Number(value)
              : null;
          const camera = {
            version: 2,
            revision,
          };
          if (rotationChanged) {
            camera.rotation = {
              lon: numberOrNull(
                changes["geo.projection.rotation.lon"] ??
                eventRotation.lon ?? projection?.rotation?.lon
              ),
              lat: numberOrNull(
                changes["geo.projection.rotation.lat"] ??
                eventRotation.lat ?? projection?.rotation?.lat
              ),
              roll: numberOrNull(
                changes["geo.projection.rotation.roll"] ??
                eventRotation.roll ?? projection?.rotation?.roll
              ),
            };
            if (camera.rotation.lon === null || camera.rotation.lat === null) return;
          }
          if (scaleChanged) {
            camera.scale = numberOrNull(changes["geo.projection.scale"]);
            if (camera.scale === null) return;
          }

          saveStoredCamera(camera);
        };

        const restoreAfterInteraction = () => {
          if (applyingCamera) return;
          const elapsed = Date.now() - lastUserCameraChange;
          if (elapsed < 700) {
            window.clearTimeout(pendingRestore);
            pendingRestore = window.setTimeout(restoreAfterInteraction, 725 - elapsed);
            return;
          }
          applySavedCamera();
        };

        graph.on("plotly_relayout", handleRelayout);
        attachedGraphs.set(graph, {
          relayout: handleRelayout,
        });
        window.requestAnimationFrame(() => {
          window.requestAnimationFrame(restoreAfterInteraction);
        });
      };

      const observer = new MutationObserver(attachToGlobe);
      observer.observe(document.body, { childList: true, subtree: true });
      attachToGlobe();

      return () => {
        observer.disconnect();
        window.clearTimeout(pendingRestore);
        const graph = document.querySelector(selector);
        const handlers = graph && attachedGraphs.get(graph);
        if (handlers && typeof graph.removeListener === "function") {
          graph.removeListener("plotly_relayout", handlers.relayout);
        }
      };
    }
    """,
    isolate_styles=False,
)


def capture_globe_camera(revision: str) -> None:
    """Mount a hidden listener that stores globe movement in session state."""
    camera = st.session_state.get(GLOBE_CAMERA_SESSION_KEY)
    if (
        not isinstance(camera, dict)
        or camera.get("version") != 2
        or camera.get("revision") != revision
    ):
        camera = None
    _camera_capture(
        data={
            "selector": ".st-key-fireline_atlas_globe .js-plotly-plot",
            "revision": revision,
            "camera": camera,
        },
        default={"camera": None},
        on_camera_change=_save_globe_camera,
        key=GLOBE_CAMERA_COMPONENT_KEY,
        width="content",
        height=0,
    )
