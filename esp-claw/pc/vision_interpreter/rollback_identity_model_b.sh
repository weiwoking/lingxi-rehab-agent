#!/usr/bin/env bash
set -euo pipefail
BACKUP_ROOT="/opt/rehab/backups/vision_interpreter_20260713005133"
if [[ "${1:-}" != "--execute" ]]; then
  cat <<'DRYRUN'
DRY RUN: would restore vision_interpreter from configured backup root.
DRY RUN: would restore /etc/systemd/system/vision-interpreter.service.
DRY RUN: would keep Model A file in the restored project.
DRY RUN: would keep current Model B files only in the moved-aside current directory.
DRY RUN: would restart vision-interpreter and verify 8010 and 8000.
Run with --execute to perform rollback.
DRYRUN
  echo "BACKUP_ROOT=$BACKUP_ROOT"
  exit 0
fi
test -d "$BACKUP_ROOT/vision_interpreter"
test -f "$BACKUP_ROOT/vision-interpreter.service"
systemctl stop vision-interpreter || true
if [[ -d /opt/rehab/vision_interpreter ]]; then
  mv /opt/rehab/vision_interpreter "/opt/rehab/vision_interpreter.rollback_kept.$(date +%Y%m%d%H%M%S)"
fi
cp -a "$BACKUP_ROOT/vision_interpreter" /opt/rehab/vision_interpreter
cp -a "$BACKUP_ROOT/vision-interpreter.service" /etc/systemd/system/vision-interpreter.service
systemctl daemon-reload
systemctl restart vision-interpreter
sleep 8
systemctl is-active --quiet vision-interpreter
curl -fsS http://127.0.0.1:8010/api/v1/vision/health >/dev/null
curl -fsS http://127.0.0.1:8000/doctor >/dev/null
ss -lntp | grep -E ':8010|:8000'
echo ROLLBACK_OK
