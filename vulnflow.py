from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import threading
import time
import venv
import webbrowser
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
VENV_DIR = ROOT_DIR / ".venv"
REQUIREMENTS_FILE = ROOT_DIR / "requirements.txt"


def _find_available_port(preferred_port: int, *, host: str = "127.0.0.1", search_window: int = 25) -> int:
    for port in range(preferred_port, preferred_port + search_window):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise SystemExit(f"Could not find a free local port in range {preferred_port}-{preferred_port + search_window - 1}.")


def _venv_python_path() -> Path:
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _is_running_in_project_venv() -> bool:
    try:
        return Path(sys.prefix).resolve() == VENV_DIR.resolve()
    except OSError:
        return False


def _require_project_venv() -> None:
    if _is_running_in_project_venv():
        return
    raise SystemExit("Project virtual environment is not active. Run 'vulnflow prepare' first.")


def _run_python_command(command: list[str], *, description: str) -> None:
    print(description)
    try:
        subprocess.run(command, cwd=ROOT_DIR, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc


def start_dashboard(*, port: int = 7337) -> int | None:
    try:
        import uvicorn
        from dashboard.server import create_app
    except ImportError:
        print("Install fastapi and uvicorn to use the dashboard.")
        return None
    selected_port = _find_available_port(port)
    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=selected_port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return selected_port


def run_start(args: argparse.Namespace) -> None:
    _require_project_venv()
    if not args.keep_db:
        from dashboard.docs_rag import clear_docs_index_database

        clear_docs_index_database(ROOT_DIR)
    port = start_dashboard(port=args.port)
    if port is None:
        raise SystemExit(1)
    time.sleep(0.75)
    if not args.no_open:
        webbrowser.open(f"http://127.0.0.1:{port}")
    print(f"VulnFlow builder: http://127.0.0.1:{port}")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("Stopping VulnFlow builder.")


def run_prepare(_: argparse.Namespace) -> None:
    if not REQUIREMENTS_FILE.is_file():
        raise SystemExit(f"Could not find requirements file: {REQUIREMENTS_FILE}")

    if VENV_DIR.exists():
        print(f"Using existing virtual environment at {VENV_DIR}.")
    else:
        print(f"Creating virtual environment at {VENV_DIR}.")
        venv.EnvBuilder(with_pip=True).create(VENV_DIR)

    venv_python = _venv_python_path()
    if not venv_python.is_file():
        raise SystemExit(f"Virtual environment Python was not created: {venv_python}")

    _run_python_command([str(venv_python), "-m", "pip", "install", "--upgrade", "pip"], description="Upgrading pip in the project virtual environment.")
    _run_python_command([str(venv_python), "-m", "pip", "install", "-r", str(REQUIREMENTS_FILE)], description="Installing project dependencies into the virtual environment.")
    print("Environment is ready. You can now run 'vulnflow start'.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vulnflow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("prepare", help="Create the project virtual environment and install dependencies.")
    start_parser = subparsers.add_parser("start", help="Launch the local pipeline builder.")
    start_parser.add_argument("--port", type=int, default=7337, help="Local server port.")
    start_parser.add_argument("--no-open", action="store_true", help="Do not open a browser tab automatically.")
    start_parser.add_argument(
        "--keep-db",
        action="store_true",
        help="Do not delete vulnflow.db (vector index) on startup.",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "prepare":
        run_prepare(args)
        return

    if args.command == "start":
        run_start(args)
        return

    parser.error(f"Unknown command: {args.command}")


if __name__ == "__main__":
    main()
