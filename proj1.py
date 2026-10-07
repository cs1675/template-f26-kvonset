#!/usr/bin/env python3
"""Run the kvonset client/server sweep in local Docker containers.

Builds a local image (from the repo's Dockerfile), compiles the binaries into a bind-mounted target/ directory, and then runs the server and client as sibling containers on a private Docker network.

For each attempted load (in keys / second), starts a fresh server with the given strategy and runs the client against it.

Usage: ./proj1.py <strategy> [--load KEYS_PER_SEC] [--server-cpus CPUS] [--seed SEED] [--flamegraph]
  e.g. ./proj1.py send-recv
       ./proj1.py io-uring --load 250000
       ./proj1.py optimized --seed 1675
       ./proj1.py vectored-io --load 500000 --flamegraph

The optimized strategy requires --seed, which is passed to the client.

With --flamegraph, the server runs under `perf` (via cargo-flamegraph) and each run writes flamegraph.svg to its server-data directory.
Profiling adds overhead, so don't report latency or throughput numbers from these runs.
"""

import argparse
import datetime
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
# names of Docker resources (image, container network, container names)
IMAGE = "kvonset-project"
NETWORK = "kvonset-net"
SERVER_NAME = "kvonset-server"
CLIENT_NAME = "kvonset-client"
# Run as the host user so bind-mounted output files aren't left root-owned.
CONTAINER_USER = f"{os.getuid()}:{os.getgid()}"

# runtime configuration
PORT = 4242
SERVER_RUNTIME_SECS = 15
CLIENT_RUNTIME_SECS = 15
TIMEOUT_SECS = 20

STRATEGIES = ["send-recv", "vectored-io", "io-uring", "optimized"]
# Attempted loads, in keys / second
LOADS_KEYS_PER_SEC = [50_000, 100_000, 250_000, 500_000, 750_000, 1_000_000, 1_250_000]


def run(cmd, **kwargs):
    print(f"$ {' '.join(cmd)}")
    kwargs.setdefault("check", True)
    return subprocess.run(cmd, **kwargs)


def rm_container(name):
    subprocess.run(
        ["docker", "rm", "-f", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def commit_hash():
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "nogit"


def build_image():
    run(["docker", "build", "-t", IMAGE, "-f", str(REPO_ROOT / "Dockerfile"), str(REPO_ROOT)])


def ensure_network():
    exists = subprocess.run(
        ["docker", "network", "inspect", NETWORK],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if exists.returncode != 0:
        run(["docker", "network", "create", NETWORK])


def build_binaries():
    run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            CONTAINER_USER,
            "-v",
            f"{REPO_ROOT}:/repo",
            "-w",
            "/repo",
            IMAGE,
            "bash",
            "-c",
            "time cargo build --release --bin server --bin client",
        ]
    )


def server_port_open():
    result = subprocess.run(
        ["docker", "exec", SERVER_NAME, "bash", "-c", f"ss -tln | grep -q :{PORT}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def start_server(run_id, strategy, data_prefix, server_cpus, flamegraph):
    out_dir = f"/repo/runs/{run_id}/server-data/{data_prefix}"
    server = (
        f"timeout {TIMEOUT_SECS} /repo/target/release/server --port {PORT} "
        f"--strategy {strategy} "
        f"--runtime-secs {SERVER_RUNTIME_SECS} "
        f"--outpath {out_dir}"
    )
    if flamegraph:
        # flamegraph writes perf.data to the working directory, so run from `out_dir`.
        # `timeout` goes inside the profiled command so that it bounds only the server, not perf's
        # post-processing.
        server = f"flamegraph --title '{data_prefix}' -o {out_dir}/flamegraph.svg -- {server}"
    command = f"mkdir -p {out_dir} && cd {out_dir} && {server} > server-stdout 2> server-stderr"
    if flamegraph:
        # perf.data is hundreds of MB per run; keep it only if generating the flamegraph failed.
        command += f" ; [ -f {out_dir}/flamegraph.svg ] && rm -f {out_dir}/perf.data"

    print(f"Executing: {command}")
    cpu_args = ["--cpuset-cpus", server_cpus] if server_cpus else []
    run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            SERVER_NAME,
            "--user",
            CONTAINER_USER,
            "--network",
            NETWORK,
            # Docker's default seccomp profile blocks io_uring.
            "--security-opt",
            "seccomp=unconfined",
            *cpu_args,
            "-v",
            f"{REPO_ROOT}:/repo",
            "-w",
            "/repo",
            IMAGE,
            "bash",
            "-c",
            command,
        ]
    )


def run_client(run_id, data_prefix, load, seed):
    out_dir = f"runs/{run_id}/client-data/{data_prefix}"
    seed_arg = f"--seed {seed}" if seed is not None else ""
    command = (
        f"mkdir -p {out_dir} && "
        f"SERVER_IP=$(getent hosts {SERVER_NAME} | awk '{{print $1}}') && "
        f" timeout {TIMEOUT_SECS} "
        f" ./target/release/client "
        f" --ip $SERVER_IP --port {PORT} "
        f" --runtime-secs {CLIENT_RUNTIME_SECS} "
        f" --load-keys-attempted {load} --outpath {out_dir} {seed_arg} "
        f" > {out_dir}/client-stdout 2> {out_dir}/client-stderr"
    )
    print(f"Executing: {command}")
    run(
        [
            "docker",
            "run",
            "--rm",
            "--name",
            CLIENT_NAME,
            "--user",
            CONTAINER_USER,
            "--network",
            NETWORK,
            "-v",
            f"{REPO_ROOT}:/repo",
            "-w",
            "/repo",
            IMAGE,
            "bash",
            "-c",
            command,
        ]
    )


def run_iter(run_id, strategy, load, server_cpus, seed, flamegraph):
    data_prefix = f"{strategy}-{load}"
    start_server(run_id, strategy, data_prefix, server_cpus, flamegraph)

    while not server_port_open():
        print("waiting for server to start")
        time.sleep(1)

    run_client(run_id, data_prefix, load, seed)

    # The server exits on its own once --runtime-secs elapses. With --flamegraph, this also waits
    # for the flamegraph to be generated.
    subprocess.run(["docker", "wait", SERVER_NAME], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("strategy", choices=STRATEGIES)
    parser.add_argument(
        "--load",
        type=int,
        default=None,
        help="Run a single attempted load (keys / second) instead of sweeping the default list",
    )
    parser.add_argument(
        "--server-cpus",
        default=None,
        help=(
            "Restrict the server container to these CPUs (docker run --cpuset-cpus), e.g. 0,1 "
            "to approximate the grading server's two cores"
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Seed for the client's request generator (required for the optimized strategy)",
    )
    parser.add_argument(
        "--flamegraph",
        action="store_true",
        help="Profile the server with perf and write flamegraph.svg to each run's server-data directory",
    )
    args = parser.parse_args()
    if args.strategy == "optimized" and args.seed is None:
        parser.error("the optimized strategy requires --seed")
    if args.seed is not None and args.seed < 0:
        parser.error("--seed must be non-negative")

    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = f"{timestamp}-{commit_hash()}"

    build_image()
    ensure_network()
    build_binaries()

    loads = [args.load] if args.load is not None else LOADS_KEYS_PER_SEC
    for load in loads:
        run_iter(run_id, args.strategy, load, args.server_cpus, args.seed, args.flamegraph)

    print(f"Done. Output data is under {REPO_ROOT / 'runs' / run_id}/")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        print(f"Command failed: {exc}", file=sys.stderr)
        rm_container(SERVER_NAME)
        rm_container(CLIENT_NAME)
        sys.exit(1)
    except KeyboardInterrupt:
        rm_container(SERVER_NAME)
        rm_container(CLIENT_NAME)
        sys.exit(1)
