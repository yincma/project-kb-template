from __future__ import annotations

import argparse
from pathlib import Path
import threading
import time
import webbrowser

import uvicorn

from .app import create_app
from .services.safety import SAFE_LOCAL_HOSTS


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the local Project KB Studio web GUI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    project_root = Path(args.project_root).expanduser().resolve()
    if args.host not in SAFE_LOCAL_HOSTS:
        print(
            "Security warning: Project KB Studio is being bound outside localhost. "
            "Only do this on a trusted network."
        )

    url = f"http://{args.host}:{args.port}"
    app = create_app(project_root, debug=args.debug)
    if not args.no_browser:
        threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    print(f"Project KB Studio running at {url}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="debug" if args.debug else "info")


def _open_browser(url: str) -> None:
    time.sleep(0.8)
    webbrowser.open(url)


if __name__ == "__main__":
    main()

