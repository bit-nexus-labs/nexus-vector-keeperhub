"""Run the one-shot KeeperHub MCP OAuth read-only handshake probe."""

from __future__ import annotations

import json

from _keeperhub_mcp_oauth_flow import run_probe
from _keeperhub_mcp_oauth_runtime import PROBE, ProbeError


def main() -> int:
    try:
        exit_code, result = run_probe()
    except KeyboardInterrupt:
        exit_code = 130
        result = {
            "probe": PROBE,
            "status": "STOP",
            "reason": "USER_CANCELLED",
            "tool_calls": 0,
            "execute_calls": 0,
            "simulation_posts": 0,
            "broadcast_posts": 0,
            "funds_moved": False,
            "retry": "MANUAL_LOCAL_RECOVERY_REQUIRED",
        }
    except ProbeError as error:
        exit_code = 2
        result = {
            "probe": PROBE,
            "status": "OUTCOME_UNKNOWN" if error.outcome_unknown else "STOP",
            "reason": error.code,
            "stage": error.stage,
            "tool_calls": 0,
            "execute_calls": 0,
            "simulation_posts": 0,
            "broadcast_posts": 0,
            "funds_moved": False,
            "retry": "MANUAL_LOCAL_RECOVERY_REQUIRED",
        }
    except Exception:
        exit_code = 2
        result = {
            "probe": PROBE,
            "status": "OUTCOME_UNKNOWN",
            "reason": "UNEXPECTED_PROBE_FAILURE",
            "tool_calls": 0,
            "execute_calls": 0,
            "simulation_posts": 0,
            "broadcast_posts": 0,
            "funds_moved": False,
            "retry": "MANUAL_REVIEW_REQUIRED",
        }
    print(json.dumps(result, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
