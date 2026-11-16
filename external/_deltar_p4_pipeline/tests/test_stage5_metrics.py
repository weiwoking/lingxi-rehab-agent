import numpy as np

from deltar_p4_pipeline.stage5_metrics import depth_metrics, distribution, geometry_errors, summarize_rows


def test_depth_and_distribution_metrics():
    metrics=depth_metrics(np.array([[1.,2.]]),np.array([[1.1,1.8]]),np.ones((1,2),bool))
    assert np.isclose(metrics["pixel_mae_m"],.15) and distribution([1,2,3])["median"]==2


def test_rejection_false_valid_summary():
    summary=summarize_rows([{"status":"valid","false_valid":False,"distance_absolute_error_m":.1},{"status":"rejected","false_valid":False}])
    assert summary["rejection_rate"]==.5 and summary["false_valid_rate"]==0
