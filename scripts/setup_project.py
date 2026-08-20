import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run(command: list[str], cwd: Path, environment: dict[str, str]) -> None:
    subprocess.run(command, cwd=cwd, env=environment, check=True)


def system_browser_available() -> bool:
    candidates = [
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("brave"),
        shutil.which("brave-browser"),
        Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        Path("C:/Program Files/BraveSoftware/Brave-Browser/Application/brave.exe"),
        Path("C:/Program Files (x86)/BraveSoftware/Brave-Browser/Application/brave.exe"),
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        Path("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
    ]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.extend(
            [
                Path(local_app_data) / "Google/Chrome/Application/chrome.exe",
                Path(local_app_data) / "Chromium/Application/chrome.exe",
                Path(local_app_data) / "BraveSoftware/Brave-Browser/Application/brave.exe",
            ]
        )
    return any(candidate and Path(candidate).exists() for candidate in candidates)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-browser", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    virtual_environment = root / ".venv"
    temporary_directory = root / ".tmp"
    temporary_directory.mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment["TEMP"] = str(temporary_directory)
    environment["TMP"] = str(temporary_directory)
    if not virtual_environment.exists():
        run([sys.executable, "-m", "venv", str(virtual_environment)], root, environment)

    if sys.platform == "win32":
        python = virtual_environment / "Scripts" / "python.exe"
    else:
        python = virtual_environment / "bin" / "python"

    run([str(python), "-m", "pip", "install", "--upgrade", "pip"], root, environment)
    run([str(python), "-m", "pip", "install", "-r", "requirements.txt"], root, environment)
    if not args.skip_browser:
        if system_browser_available():
            print("Using an installed Chrome, Chromium, or Brave browser.")
        else:
            run([str(python), "-m", "playwright", "install", "chromium"], root, environment)
    run([str(python), "-m", "pytest", "-p", "no:cacheprovider"], root, environment)


if __name__ == "__main__":
    main()
