# template/load_template.py

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scoring_assets import TEMPLATE_ROOT

DEFAULT_TEMPLATE_PATH = TEMPLATE_ROOT / "MHH_healthy_template.json"


def load_healthy_template(template_path=DEFAULT_TEMPLATE_PATH):
    """
    加载健康模板 JSON。

    注意：
    JSON 里的 key 会自动变成字符串，
    所以这里要把 TaskNumber 的 key 转回 int。
    """

    with open(template_path, "r", encoding="utf-8") as f:
        template_raw = json.load(f)

    template = {}

    for task_key, task_template in template_raw.items():
        template[int(task_key)] = task_template

    return template
