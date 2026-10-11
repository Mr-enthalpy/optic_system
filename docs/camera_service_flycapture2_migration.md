# Camera sidecar

Camera SDK calls are isolated in `devices/camera_backend_flycapture2.py` and the camera sidecar. The main process uses CameraServiceClient ZMQ RPC; shared-memory frames are consumed by a private FrameStreamClient. PUB transports frame metadata rather than pixels.

The optional external package is `flycapture2_c`. Install it into the selected environment; use `FLYCAPTURE2_SDK_DIR` and `FLYCAPTURE2_DLL_DIR` for driver paths. `OPTIC_SYSTEM_SIDECAR_PYTHON` can explicitly select the sidecar interpreter. No source path or neighboring checkout is assumed.

Startup is headless: PreConfigGUI returns a rejection and is absent from normal GUI startup. OpenCamera defaults to disabling trigger; start/stop streaming are explicit operations. Camera configuration uses protocol commands such as SetPixelFormat, SetROI, SetProperty and SnapshotProperties. The GUI does not import the hardware SDK.

Capture averages 2D raw sensor frames with a float64 accumulator and retains metadata; preview demosaicing is display-only. A verified full-sensor frame extent is required for survey conversion. If SDK metadata cannot establish it, supply the measured/verified camera.frame_extent in the plan; do not infer a full sensor from the size of a crop.
