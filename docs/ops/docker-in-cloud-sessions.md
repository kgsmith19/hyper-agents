# Docker in Claude Code cloud sessions

Working notes for running Docker inside the cloud container. Keep this current: when something
here stops being true, fix the note in the same change.

Last verified: 2026-10-30, Docker Engine 29.9.2 client and daemon, overlayfs.

## Summary

- The `docker` CLI is installed, but no daemon runs by default. `docker info` fails with
  `/var/run/docker.sock: no such file or directory`.
- `dockerd` can be started by hand as root and works with overlayfs.
- Image pulls from `ghcr.io` work. Pulls from Docker Hub fail with HTTP 429 (rate limit).
- Containers start, but any container whose `--ulimit nofile` exceeds the sandbox hard limit
  (20000) fails at runc init. The OpenHands SDK's `DockerWorkspace` hardcodes `nofile=65536`, so it
  cannot start here.

## Start the daemon

```sh
dockerd --host=unix:///var/run/docker.sock \
  --default-ulimit nofile=$(ulimit -Hn):$(ulimit -Hn) > /tmp/dockerd.log 2>&1 &
sleep 8 && docker info --format '{{.ServerVersion}}'
```

- The `--default-ulimit` flag matches the sandbox hard limit. Without it, the default
  `nofile` of the daemon's containers can be above the limit and fail with `error setting rlimit
  type 7: operation not permitted`.
- The daemon does not survive a session reset. Put the command in the environment setup script
  to make it stick (cloud environment menu, then Edit).

## What worked

- `docker run --rm ghcr.io/containerd/busybox:latest echo ok`: prints `ok`.
- `docker pull ghcr.io/openhands/agent-server:1.58.2-python`: pulls.
- `docker run -d --name agent-smoke -p 18010:8000 ghcr.io/openhands/agent-server:1.58.2-python --host 0.0.0.0 --port 8000`
  followed by `curl http://127.0.0.1:18010/health` returns `200 {"status":"ok"}`. No LLM key needed.
  Remove the container afterwards with `docker rm -f agent-smoke`.

## What did not work

- `docker run --rm hello-world` and other Docker Hub images: HTTP 429 from
  `registry-1.docker.io`. Use `ghcr.io` mirrors where possible, or retry later.
- `DockerWorkspace` from `openhands-workspace` 1.58.2: the SDK passes `--ulimit nofile=65536:65536`
  to `docker run`, which fails in this sandbox with `error setting rlimit type 7: operation not
  permitted`. The hard limit cannot be raised here (`ulimit -Hn 65536` fails, and `CapEff` lacks
  `CAP_SYS_RESOURCE`). Each `docker run` needs a limit at or below 20000 to start in this sandbox.
- Raising the daemon's limit does not help: the SDK's per-container `--ulimit` overrides the
  daemon default.

## Open issues

- The key-free tier of AC4 (start `DockerWorkspace`, check `/health`) cannot run in this sandbox
  because of the nofile limit above. It passes on GitHub-hosted `ubuntu-latest`: run
  https://github.com/kgsmith19/hyper-agents/actions/runs/41154276833 (2026-10-31), 97 passed, 0 skipped.
- The LLM-key tier of AC4 needs a repository secret. It must not run on forks or untrusted PRs.

## Checklist for the next session

1. `docker info` shows a server version. If not, start the daemon as above.
2. `ulimit -Hn` is still 20000 (or the value the daemon was started with).
3. Pull only `ghcr.io` images, or retry Docker Hub later.
4. Run the SDK workspace test (`tests/integration/test_docker_workspace.py`) on GitHub CI, not here. It fails
   locally with the nofile error, and it passes on hosted runners.
