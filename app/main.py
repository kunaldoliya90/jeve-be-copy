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
    """Run the API with auto-reload, for development.

    ``reload_includes`` is required rather than left to uvicorn's default: unset, its watcher
    only globs ``*.py`` (see ``uvicorn.supervisors.statreload``), so an edit to a business-rule
    YAML file - which the business owner, not a code change, is meant to make - never triggers
    a reload. The server then keeps answering from whatever context it loaded at startup,
    which reads exactly like the fix was not applied.
    """
    uvicorn.run(
        APP_IMPORT_PATH,
        host=host,
        port=port,
        reload=True,
        app_dir=APP_DIR,
        reload_dirs=[APP_DIR],
        reload_includes=["*.py", "*.yaml", "*.yml"],
    )


if __name__ == "__main__":
    cli()
