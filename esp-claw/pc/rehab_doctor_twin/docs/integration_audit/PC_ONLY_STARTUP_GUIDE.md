# PC scoring launcher

The canonical daily entry is:

```text
C:\ec\esp-claw\tools\ESP_Claw_PC_Launcher\START_ESP_CLAW_PC.cmd
```

The command resolves the repository from its own `%~dp0` path, so the caller's current directory does not matter. It performs these operations in order:

1. removes the prior launcher-owned frontend and Doctor processes;
2. takes over any TCP 8020 listener;
3. starts and health-checks Score;
4. starts and health-checks Doctor, including UDP 6006 ownership;
5. starts and HTTP-checks the frontend;
6. saves the process state, opens `http://127.0.0.1:5173`, and keeps one status window open.

Running START again replaces the previous scoring stack. A separate STOP command is not part of the normal workflow. Closing the status window does not stop services.

TCP 8001, UDP 6006, and TCP 5173 are stopped only when their PID and creation identity match the saved launcher state. An unknown owner makes startup fail closed. TCP 8020 is the sole unconditional takeover exception. The launcher does not enumerate or stop 8000, 8030, 8040, 8050, 8765, or UDP 5005.

Logs and process state are stored outside the repository under:

```text
%LOCALAPPDATA%\ESP-Claw\doctor_pc_only
```
