from pathlib import Path

from deltar_p4_pipeline.official_object_benchmark import load_hdf5, official_samples, read_json


def test_all_287_official_hdf5_paths_and_contract():
    samples = official_samples()
    assert len(samples) == 287
    assert all(Path(row["hdf5_path"]).is_file() for row in samples)
    first = load_hdf5(samples[0])
    assert first["rgb"].shape == (480, 640, 3)
    assert first["depth"].shape == (480, 640)
    assert first["hist_data"].shape == (64, 2)
    assert first["fr"].shape == (64, 4)
    assert first["mask"].shape == (64,)


def test_generated_pipeline_has_no_gt_inference_leakage():
    path = Path("outputs_stage5_2a/new_chain_results.json")
    if not path.exists():
        return
    document = read_json(path)
    assert document["gt_used_for_inference"] is False
    assert all(row["inference_contract"]["gt_used_for_inference"] is False for row in document["results"])
