from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"


def ensure_src_path() -> None:
    src = str(SRC)
    if src not in sys.path:
        sys.path.insert(0, src)


def run_server() -> None:
    ensure_src_path()
    from print_gateway.server import main as server_main

    server_main()


def run_agent(argv: list[str]) -> None:
    ensure_src_path()
    from print_gateway.agent import main as agent_main

    sys.argv = ["print-gateway-agent", *argv]
    agent_main()


def build_frontend() -> None:
    subprocess.run(["npm", "run", "build"], cwd=ROOT, check=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Linux Print Gateway launcher")
    parser.add_argument(
        "command",
        nargs="?",
        default="server",
        choices=["server", "agent", "build-frontend"],
        help="Command to run. Defaults to server.",
    )
    args, remainder = parser.parse_known_args(argv)
    args.remainder = remainder
    return args


def main() -> None:
    args = parse_args(sys.argv[1:])
    if args.command == "server":
        run_server()
        return
    if args.command == "agent":
        run_agent(args.remainder)
        return
    if args.command == "build-frontend":
        build_frontend()
        return
    raise SystemExit(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    main()
