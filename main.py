from __future__ import annotations

import multiprocessing
import signal
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"

LOCAL_HOSTS = frozenset({"0.0.0.0", "::"})
SERVER_READY_TIMEOUT_SECONDS = 30
PROCESS_STOP_TIMEOUT_SECONDS = 5


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


def _server_base_url() -> str:
    ensure_src_path()
    from print_gateway.config import get_settings

    settings = get_settings()
    host = "127.0.0.1" if settings.host in LOCAL_HOSTS else settings.host
    return f"http://{host}:{settings.port}"


def wait_for_server(url: str, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.5)
    return False


def _raise_keyboard_interrupt(signum: int, frame: object) -> None:
    del signum, frame
    raise KeyboardInterrupt


def _stop_process(process: multiprocessing.process.BaseProcess) -> None:
    if not process.is_alive():
        return
    process.terminate()
    process.join(timeout=PROCESS_STOP_TIMEOUT_SECONDS)
    if process.is_alive():
        process.kill()
        process.join()


def main() -> None:
    """Start the Server and Agent together on a single host."""
    base_url = _server_base_url()

    ctx = multiprocessing.get_context("spawn")
    server_process = ctx.Process(target=run_server, name="print-gateway-server")
    server_process.start()

    try:
        if not wait_for_server(f"{base_url}/api/service", SERVER_READY_TIMEOUT_SECONDS):
            raise SystemExit("server did not become ready in time")
        agent_process = ctx.Process(
            target=run_agent,
            args=(["--server", base_url],),
            name="print-gateway-agent",
        )
        agent_process.start()
    except BaseException:
        _stop_process(server_process)
        raise

    processes = [server_process, agent_process]
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
    try:
        while all(process.is_alive() for process in processes):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        for process in processes:
            _stop_process(process)


if __name__ == "__main__":
    main()
