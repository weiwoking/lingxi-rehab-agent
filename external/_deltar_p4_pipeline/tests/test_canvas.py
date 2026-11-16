import numpy as np

from deltar_p4_pipeline.canvas import apply_canvas, regular_grid_source, transform_fr, transform_intrinsics


RGB = np.zeros((360, 640, 3), dtype=np.uint8)


def test_letterbox_matrix_region_and_intrinsics() -> None:
    result = apply_canvas(RGB, "letterbox_640x480")
    assert result.rgb.shape == (480, 640, 3)
    assert result.valid_region == (60, 0, 420, 640)
    assert int(result.valid_mask.sum()) == 360 * 640
    assert np.allclose(result.source_to_canvas, [[1, 0, 0], [0, 1, 60], [0, 0, 1]])
    transformed = transform_intrinsics({"fx": 500, "fy": 500, "cx": 320, "cy": 180}, result.source_to_canvas)
    assert transformed["fx"] == 500 and transformed["fy"] == 500
    assert transformed["cx"] == 320 and transformed["cy"] == 240


def test_resize_matrix_and_warning() -> None:
    result = apply_canvas(RGB, "resize_640x480")
    assert np.isclose(result.source_to_canvas[1, 1], 4 / 3)
    assert "GEOMETRIC_DISTORTION_NON_UNIFORM_SCALE" in result.warnings


def test_center_crop_is_invertible_and_reports_loss() -> None:
    result = apply_canvas(RGB, "scale_to_fill_center_crop_640x480")
    assert np.allclose(result.source_to_canvas @ result.canvas_to_source, np.eye(3))
    assert result.metadata["crop_left"] == 106
    assert result.metadata["scale_x"] == 853 / 640
    assert result.metadata["scale_y"] == 480 / 360
    assert result.source_to_canvas[0, 0] == 853 / 640
    assert result.source_to_canvas[1, 1] == 480 / 360
    assert result.metadata["lost_fov_ratio"] > 0.24


def test_center_crop_points_fr_intrinsics_and_roundtrip_use_actual_raster() -> None:
    result = apply_canvas(RGB, "scale_to_fill_center_crop_640x480")
    points = np.asarray([[0.0, 0.0, 1.0], [640.0, 0.0, 1.0], [0.0, 360.0, 1.0], [640.0, 360.0, 1.0], [320.0, 180.0, 1.0]])
    mapped = (result.source_to_canvas @ points.T).T
    assert np.allclose(mapped[:, :2], [[-106, 0], [747, 0], [-106, 480], [747, 480], [320.5, 240]])
    roundtrip = (result.canvas_to_source @ mapped.T).T
    assert np.max(np.abs(roundtrip - points)) < 1e-10
    fr = np.tile([0.0, 0.0, 360.0, 640.0], (64, 1))
    assert np.allclose(transform_fr(fr, result.source_to_canvas)[0], [0.0, -106.0, 480.0, 747.0])
    intrinsics = transform_intrinsics({"fx": 500, "fy": 500, "cx": 320, "cy": 180}, result.source_to_canvas)
    assert intrinsics["fx"] == 500 * 853 / 640
    assert intrinsics["fy"] == 500 * 480 / 360
    assert intrinsics["cx"] == 320.5 and intrinsics["cy"] == 240


def test_regular_grid_and_fr_half_open_transform() -> None:
    grid = regular_grid_source()
    assert grid.shape == (64, 4)
    transformed = transform_fr(grid, apply_canvas(RGB, "letterbox_640x480").source_to_canvas)
    assert transformed[0].tolist() == [60.0, 0.0, 105.0, 80.0]
    assert transformed[-1].tolist() == [375.0, 560.0, 420.0, 640.0]


def test_direct_probe_shapes_are_truthful() -> None:
    assert apply_canvas(RGB, "direct_640x360").rgb.shape[:2] == (360, 640)
    assert apply_canvas(RGB, "direct_640x384_pad32").rgb.shape[:2] == (384, 640)
