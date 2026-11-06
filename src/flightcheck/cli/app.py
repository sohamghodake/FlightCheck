"""Typer application. Parses arguments only; no business logic lives here."""

from typing import Annotated

import typer

from flightcheck import __version__

HELP = """\
FlightCheck checks Python AI applications for BharatCode API integration mistakes.

\b
Commands coming in later phases (not available yet):
  scan PATH                 static analysis against BharatCode's documented contract
  test PATH --allow-exec    run the app against a local traffic-recording mock
  explain REPORT.json       opt-in explanations of findings using BharatCode
  report REPORT.json        render a saved report (HTML or Markdown)
"""

app = typer.Typer(
    name="flightcheck",
    help=HELP,
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode=None,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"flightcheck {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def root(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            help="Show the FlightCheck version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
    ] = False,
) -> None:
    """FlightCheck checks Python AI applications for BharatCode API integration mistakes."""


def main() -> None:
    """Console-script entry point."""
    app()
