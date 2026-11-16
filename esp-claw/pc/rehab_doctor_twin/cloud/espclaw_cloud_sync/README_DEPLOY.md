# ESP-Claw Cloud Sync Deployment Notes

Current status: this package prepares the cloud FastAPI service source only. It does not mean
the Aliyun server is deployed or that the public health endpoint is ready.

Target layout:

- `/opt/espclaw_cloud_sync`: service source and virtual environment
- `/var/lib/espclaw_cloud_sync`: SQLite database, uploaded `.csv.gz`, staging files
- `/etc/espclaw-cloud-sync.env`: token and runtime configuration
- `/etc/systemd/system/espclaw-cloud-sync.service`: systemd unit
- Nginx location: `/api/v1/cloud/` -> `http://127.0.0.1:8040`

The env file must be created on the server and must not be copied into Git or delivery bundles:

```env
ESPCLAW_CLOUD_API_TOKEN=<same random token as local PC>
ESPCLAW_CLOUD_DATA_ROOT=/var/lib/espclaw_cloud_sync
ESPCLAW_CLOUD_PORT=8040
ESPCLAW_CLOUD_MAX_UPLOAD_BYTES=26214400
```

Run the service as the dedicated `espclaw-cloud` user, not as root.

Next deployment steps:

1. Generate a dedicated SSH key pair for the next deployment turn.
2. The user performs one interactive SSH login and adds the public key to `/root/.ssh/authorized_keys`.
3. Copy this service source to `/opt/espclaw_cloud_sync`.
4. Create the server environment file with the shared API token.
5. Configure systemd and Nginx.
6. Verify `GET /api/v1/cloud/health` returns `{"status":"ready"}`.
7. Verify authentication, duplicate upload idempotency, and SHA conflict handling.
8. Only after health is verified, set the local PC AppData `ESPCLAW_CLOUD_SYNC_ENABLED=true`.
9. Start the local doctor system and use manual upload, or enable the runtime-only automatic upload checkbox.

The automatic upload checkbox is intentionally not persistent; every backend restart restores it to off.
Do not create scripts that embed SSH passwords, API tokens, subject salts, or private keys.
