"""Hardware-free checks for the live alignment view."""
from __future__ import annotations

import gzip
import http.client
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts" / "udc_hdr_psf"
sys.path.insert(0, str(SCRIPTS))
import align_live as al  # noqa: E402

SHAPE = (400, 600)
BLACK = 64.0


def _frame(centre, sigma=3.0, peak=800.0):
    yy, xx = np.mgrid[0:SHAPE[0], 0:SHAPE[1]]
    spot = peak * np.exp(-((xx - centre[0]) ** 2 + (yy - centre[1]) ** 2) / (2 * sigma ** 2))
    return BLACK + spot


def test_box_mean_of_a_constant_image_is_that_constant():
    image = np.full((10, 12), 7.0)
    assert np.allclose(al.box_mean(image, radius=2), 7.0)


def test_box_mean_of_a_hot_pixel_is_below_the_pixel():
    image = np.zeros((11, 11))
    image[5, 5] = 25.0
    smoothed = al.box_mean(image, radius=2)
    assert abs(smoothed[5, 5] - 1.0) < 1e-9
    assert smoothed.sum() == 25.0


def test_block_mean_preserves_the_total_and_the_shape():
    image = np.arange(24, dtype=float).reshape(4, 6)
    shrunk = al.block_mean(image, 2)
    assert shrunk.shape == (2, 3)
    assert abs(shrunk.sum() * 4 - image.sum()) < 1e-9
    assert np.array_equal(al.block_mean(image, 1), image)


def test_detect_finds_a_fractional_centre():
    truth = (301.35, 198.6)
    found = al.detect(_frame(truth), BLACK)
    assert found["spot_found"]
    assert abs(found["centre_xy"][0] - truth[0]) < 0.2
    assert abs(found["centre_xy"][1] - truth[1]) < 0.2
    assert found["centre_usable"]


def test_detect_reports_two_radii_and_they_agree_on_a_clean_spot():
    truth = (301.35, 198.6)
    found = al.detect(_frame(truth, sigma=6.0), BLACK)
    assert found["radii_px"] == [5.0, 10.0]
    assert abs(found["centre_xy_r10"][0] - truth[0]) < 0.3
    assert found["radius_shift_px"] < 0.35


def test_a_single_hot_pixel_does_not_hijack_the_centre():
    truth = (301.35, 198.6)
    frame = _frame(truth)
    frame[40, 500] = 4095.0        # a stuck pixel far from the spot
    found = al.detect(frame, BLACK)
    assert abs(found["centre_xy"][0] - truth[0]) < 0.2
    assert abs(found["centre_xy"][1] - truth[1]) < 0.2


def test_an_empty_frame_is_reported_as_no_spot():
    found = al.detect(np.full(SHAPE, BLACK), BLACK)
    assert not found["spot_found"]


def test_crop_is_centred_on_the_target():
    frame = _frame((301.35, 198.6))
    patch, origin = al.crop(frame, (300.0, 200.0), BLACK, 20)
    assert patch.shape == (41, 41)
    assert origin == (280, 180)
    # The crop is black-subtracted, so the background reads about zero.
    assert abs(float(np.median(patch)) - 0.0) < 2.0
    assert float(patch.max()) > 700.0


def test_crop_stays_inside_a_corner_window():
    frame = _frame((10.0, 10.0))
    patch, origin = al.crop(frame, (2.0, 3.0), BLACK, 20)
    assert patch.shape == (41, 41)
    assert origin[0] >= 0 and origin[1] >= 0


def test_state_reports_the_offset_and_both_roll_references():
    measured = {"centre_xy": [1613.5, 1210.0], "centre_xy_r10": [1613.4, 1210.1],
                "radius_shift_px": 0.14, "peak_xy": [1614.0, 1210.0],
                "peak_dn": 959.0, "box_mean_dn": 700.0, "spot_found": True,
                "at_frame_edge": False, "centre_usable": True, "centre_drift_px": 0.1}
    tilt = {"roll_deg": -1.4, "tilt_deg": -2.0, "roll_spread_tail_deg": 0.02,
            "buffer_mixed": False}
    references = {"p1": {"label": "P1", "roll_deg": -0.920},
                  "p2": {"label": "P2", "roll_deg": 17.247}}
    state = al.make_state(3, measured, tilt, (1610.0, 1214.0), references, [], 450, 2000,
                          seconds=2.4)
    assert state["delta_xy"] == [3.5, -4.0]
    assert state["seconds"] == 2.4
    assert abs(state["delta_p1_deg"] - (-0.48)) < 1e-9
    assert abs(state["delta_p2_deg"] - (-18.647)) < 1e-9


def test_state_without_a_roll_reading_reports_null_deltas():
    measured = {"centre_xy": [1.0, 1.0], "peak_xy": [1.0, 1.0], "peak_dn": 1.0,
                "box_mean_dn": 1.0, "spot_found": True, "at_frame_edge": False,
                "centre_usable": True, "centre_drift_px": 0.0}
    state = al.make_state(1, measured, {}, (0.0, 0.0),
                          {"p1": {"roll_deg": None}}, [], 450, 2000)
    assert state["delta_p1_deg"] is None
    assert state["delta_p2_deg"] is None
    assert state["centre_xy_r10"] is None


def test_the_overview_keeps_a_spot_that_would_fall_outside_a_crop():
    """A shrunk whole frame must show the corners; a crop must not."""
    frame = np.full((300, 400), BLACK)
    frame[8:13, 386:391] = 900.0            # far corner, far outside any 461 px crop
    overview = al.block_mean(frame, 4) - BLACK
    assert overview.shape == (75, 100)
    iy, ix = np.unravel_index(int(np.argmax(overview)), overview.shape)
    assert overview.max() > 10.0
    assert iy < 6 and ix > 90


def test_the_zoom_follows_the_spot_and_falls_back_to_the_target():
    found = {"spot_found": True, "centre_xy": [1612.4, 1211.6]}
    assert al.zoom_anchor(found, (1610.0, 1214.0)) == (1612.4, 1211.6)
    missing = {"spot_found": False, "centre_xy": [1530.4, 1143.7]}
    assert al.zoom_anchor(missing, (1610.0, 1214.0)) == (1610.0, 1214.0)


def test_a_paused_view_keeps_the_last_frame_instead_of_going_blank():
    """adb and the camera have one owner, so a bracket pauses the view.

    The page must keep the last good frame and say why it is not moving, not
    flash an error that looks like a failed capture.
    """
    last = {"index": 12, "ok": True, "spot_found": True,
            "patch_overview": [[1, 2], [3, 4]], "delta_xy": [4.0, -7.0],
            "utc": "2026-11-27T00:20:00+00:00"}
    paused = al.pause_state(last, "bracket P3_550nm")
    assert paused["paused"] is True
    assert paused["pause_reason"] == "bracket P3_550nm"
    assert paused["patch_overview"] == last["patch_overview"]
    assert paused["delta_xy"] == last["delta_xy"]
    assert last.get("paused") is None          # the caller's frame is not mutated


def test_a_pause_before_the_first_frame_does_not_claim_a_measurement():
    paused = al.pause_state(None, "")
    assert paused["paused"] is True
    assert paused["ok"] is False
    assert paused["pause_reason"]


# --- the published page -----------------------------------------------------

def _published(tmp_path):
    live = al.LiveResources()
    live.put("index.html", b"<html>view</html>")
    live.put("state.json", b'{"index": 7}', etag="e1")
    server = al.serve(tmp_path, 0, live)
    return live, server, server.server_address[1]


def _get(port, path, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request("GET", path, headers=headers or {})
    response = connection.getresponse()
    body = response.read()
    connection.close()
    return response, body


def test_the_page_is_served_from_memory_not_from_the_disk_file(tmp_path):
    _, server, port = _published(tmp_path)
    try:
        response, body = _get(port, "/")
        assert response.status == 200
        assert body == b"<html>view</html>"
        assert not (tmp_path / "index.html").exists()
    finally:
        server.shutdown()


def test_an_unchanged_state_is_refused_with_304_and_no_body(tmp_path):
    _, server, port = _published(tmp_path)
    try:
        response, body = _get(port, "/state.json")
        assert response.status == 200
        assert response.getheader("ETag") == "e1"
        assert body == b'{"index": 7}'

        same, empty = _get(port, "/state.json?etag=e1")
        assert same.status == 304
        assert empty == b""

        moved, again = _get(port, "/state.json?etag=e0")
        assert moved.status == 200 and again == b'{"index": 7}'
    finally:
        server.shutdown()


def test_the_state_is_gzipped_when_the_client_accepts_it(tmp_path):
    _, server, port = _published(tmp_path)
    try:
        response, body = _get(port, "/state.json?etag=old",
                              {"Accept-Encoding": "gzip"})
        assert response.getheader("Content-Encoding") == "gzip"
        assert gzip.decompress(body) == b'{"index": 7}'
    finally:
        server.shutdown()


def test_write_json_uses_the_bytes_it_was_given(tmp_path):
    target = tmp_path / "state.json"
    al.write_json(target, None, body=b'{"index": 1}')
    assert target.read_bytes() == b'{"index": 1}'
    al.write_json(target, {"index": 2})
    assert json.loads(target.read_text(encoding="utf-8")) == {"index": 2}


# --- the phone must not keep the live frames ---------------------------------

class _RecordingDevice:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def shell(self, args):
        self.calls.append(list(args))
        if self.fail:
            raise RuntimeError("adb said no")
        return ""


class _Phone:
    PACKAGE = "org.udc.rawprobe"


def test_a_pulled_view_frame_is_deleted_from_the_phone():
    device = _RecordingDevice()
    status = al.drop_view_run(device, _Phone, "run_1795748877894_c116b861")
    assert status == "deleted"
    assert device.calls == [["run-as", _Phone.PACKAGE, "rm", "-rf",
                             "files/captures/run_1795748877894_c116b861"]]


def test_the_frame_can_be_kept_on_request():
    device = _RecordingDevice()
    assert al.drop_view_run(device, _Phone, "run_1_aaaaaaaa", keep=True) == "kept"
    assert device.calls == []


def test_a_failed_cleanup_is_reported_and_never_raises():
    device = _RecordingDevice(fail=True)
    status = al.drop_view_run(device, _Phone, "run_1_aaaaaaaa")
    assert status == "keep-failed:RuntimeError"


def test_a_timing_map_may_hold_a_string_without_breaking_the_page():
    timings = {"capture_s": 2.7, "pull_s": 0.68, "phone": "deleted"}
    rendered = {key: (round(float(value), 2)
                      if isinstance(value, (int, float)) else value)
                for key, value in timings.items()}
    assert rendered == {"capture_s": 2.7, "pull_s": 0.68, "phone": "deleted"}
