"""Command-line entry point (like Django's manage.py): ``python app/main.py runserver``."""

from pathlib import Path

import typer
import uvicorn

from core.consts import APP_IMPORT_PATH, RUNSERVER_HOST, RUNSERVER_PORT

APP_DIR = str(Path(__file__).resolve().parent)

cli = typer.Typer()


@cli.callback()
def main() -> None:
    """JEVE-BE management commands."""


@cli.command()
def runserver(host: str = RUNSERVER_HOST, port: int = RUNSERVER_PORT) -> None:
    """Run the API with auto-reload, for development."""
    uvicorn.run(
        APP_IMPORT_PATH, host=host, port=port, reload=True, app_dir=APP_DIR, reload_dirs=[APP_DIR]
    )


if __name__ == "__main__":
    cli()
