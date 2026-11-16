#!/usr/bin/env bash
set -euo pipefail

cd /opt/rehab/vision_interpreter
apt-get update
apt-get install -y python3-venv python3-pip

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

if [[ ! -f models/yolo11n.pt ]]; then
    MODEL="$(find /opt/rehab/cloud_rehab_server -type f -name yolo11n.pt | head -n 1 || true)"
    if [[ -z "${MODEL}" ]]; then
        echo "yolo11n.pt not found. Copy it to models/yolo11n.pt first."
        exit 1
    fi
    cp "${MODEL}" models/yolo11n.pt
fi

cp deploy/vision-interpreter.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now vision-interpreter
