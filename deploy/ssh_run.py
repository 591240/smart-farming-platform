# -*- coding: utf-8 -*-
"""SSH helper for 43.142.92.229 (password auth via paramiko).
Usage: python ssh_run.py "command"
Reads password from env SF_SSH_PW to avoid hardcoding.
"""
import os
import sys

import paramiko

HOST = "43.142.92.229"
USER = os.environ.get("SF_SSH_USER", "ubuntu")
PW = os.environ["SF_SSH_PW"]

cmd = sys.argv[1] if len(sys.argv) > 1 else "echo ok"

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username=USER, password=PW, timeout=15,
            look_for_keys=False, allow_agent=False)
stdin, stdout, stderr = cli.exec_command(cmd, timeout=300)
out = stdout.read().decode("utf-8", "replace")
err = stderr.read().decode("utf-8", "replace")
rc = stdout.channel.recv_exit_status()
sys.stdout.write(out)
if err:
    sys.stderr.write(err)
sys.exit(rc)
