"""Command-line entry point for OCI A1 Capacity Hunter."""

from __future__ import annotations

import argparse
import sys
import threading

import uvicorn

from app import __version__
from app.config import AppConfig, load_config, read_ssh_public_key
from app.notifier import Notifier
from app.provisioner import Provisioner, create_oci_clients
from app.state import AppState
from app.tui import run_tui


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="oci-a1-capacity-hunter",
        description="Find OCI Ampere A1 host capacity with rate-aware retries and local status views.",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--cli", action="store_true", help="run the Rich terminal dashboard")
    modes.add_argument("--web", action="store_true", help="run the local web dashboard")
    modes.add_argument("--both", action="store_true", help="run the terminal and web dashboards together")
    modes.add_argument("--test-alert", action="store_true", help="send a test notification without calling OCI")
    modes.add_argument("--check-config", action="store_true", help="validate local config and key files without calling OCI")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def _validate_local_files(config: AppConfig) -> None:
    read_ssh_public_key(config)
    sdk_config, _, _, _ = create_oci_clients(config)
    if not sdk_config.get("tenancy"):
        raise ValueError("The selected OCI profile does not define a tenancy OCID")


def _run_terminal(provisioner: Provisioner, state: AppState) -> None:
    worker = threading.Thread(target=provisioner.run, name="oci-provisioner", daemon=True)
    worker.start()
    try:
        run_tui(state)
    except KeyboardInterrupt:
        state.request_shutdown()
    finally:
        state.request_shutdown()
        worker.join(timeout=10)


def _run_web(config: AppConfig, state: AppState, provisioner: Provisioner, notifier: Notifier) -> None:
    from app.web.server import create_app

    application = create_app(state, provisioner, notifier)
    uvicorn.run(
        application,
        host=config.ui.web_host,
        port=config.ui.web_port,
        log_level="info",
        access_log=False,
    )


def _run_both(config: AppConfig, state: AppState, provisioner: Provisioner, notifier: Notifier) -> None:
    from app.web.server import create_app

    application = create_app(state, provisioner, notifier)
    server = uvicorn.Server(uvicorn.Config(
        application,
        host=config.ui.web_host,
        port=config.ui.web_port,
        log_level="warning",
        access_log=False,
    ))
    server_thread = threading.Thread(target=server.run, name="oci-web-dashboard", daemon=True)
    server_thread.start()
    try:
        run_tui(state)
    except KeyboardInterrupt:
        state.request_shutdown()
    finally:
        state.request_shutdown()
        server.should_exit = True
        server_thread.join(timeout=10)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config()
        notifier = Notifier(config)
        if args.check_config:
            _validate_local_files(config)
            print("Configuration, OCI profile, and SSH public key are valid. No OCI API calls were made.")
            return 0
        if args.test_alert:
            channels = notifier.send_test()
            print("Test notification sent to: " + ", ".join(channels))
            return 0

        state = AppState()
        provisioner = Provisioner(config, state, notifier)
        if args.cli:
            _run_terminal(provisioner, state)
        elif args.web:
            _run_web(config, state, provisioner, notifier)
        elif args.both:
            _run_both(config, state, provisioner, notifier)
        elif config.ui.enable_web and config.ui.enable_tui:
            _run_both(config, state, provisioner, notifier)
        elif config.ui.enable_web:
            _run_web(config, state, provisioner, notifier)
        else:
            _run_terminal(provisioner, state)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
