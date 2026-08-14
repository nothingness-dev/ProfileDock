import argparse
import os
import subprocess
import sys
from pathlib import Path


def run(command: list[str], cwd: Path, environment: dict[str, str]) -> None:
    subprocess.run(command, cwd=cwd, env=environment, check=True)


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
    run([sys.executable, "-m", "venv", str(virtual_environment)], root, environment)

    if sys.platform == "win32":
        python = virtual_environment / "Scripts" / "python.exe"
    else:
        python = virtual_environment / "bin" / "python"

    run([str(python), "-m", "pip", "install", "--upgrade", "pip"], root, environment)
    run([str(python), "-m", "pip", "install", "-r", "requirements.txt"], root, environment)
    if not args.skip_browser:
        run([str(python), "-m", "playwright", "install", "chromium"], root, environment)
    run([str(python), "-m", "pytest", "-p", "no:cacheprovider"], root, environment)


if __name__ == "__main__":
    main()
