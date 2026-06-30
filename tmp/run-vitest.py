#!/usr/bin/env python3
"""Invoke the web vitest suite and print a basic summary."""
import os
import subprocess
import sys

WEB_ROOT = "/home/AI02/Documents/quantaeye/multi_agents_platform/web"
cmd = [os.path.join(WEB_ROOT, "node_modules", ".bin", "vitest"), "run", "--reporter=basic"]
result = subprocess.run(cmd, cwd=WEB_ROOT, capture_output=True, text=True)
sys.stdout.write(result.stdout)
sys.stderr.write(result.stderr)
sys.exit(result.returncode)
