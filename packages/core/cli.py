"""
Command Line Interface for UHR.
"""

from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(help="Universal Hardware Runtime CLI")
console = Console()


@app.command()
def server(
    host: str = typer.Option(None, help="Bind host"),
    port: int = typer.Option(None, help="Bind port"),
    reload: bool = typer.Option(False, help="Enable auto-reload"),
) -> None:
    """Start the API and Web Dashboard server."""
    import uvicorn

    from packages.core.api import create_app
    from packages.core.config import load_config

    cfg = load_config()

    # Override config with CLI flags
    if host:
        cfg.server.host = host
    if port:
        cfg.server.port = port
    if reload:
        cfg.server.reload = True

    fastapi_app = create_app(cfg)

    uvicorn.run(
        fastapi_app,
        host=cfg.server.host,
        port=cfg.server.port,
        workers=cfg.server.workers,
        reload=cfg.server.reload and cfg.is_development,
        log_level=cfg.logging.level.lower(),
    )


@app.command()
def diagnostics() -> None:
    """Run local system diagnostics."""
    import platform
    import shutil
    import sys

    from packages.core.config import RUNTIME_VERSION

    console.print(f"[bold green]Universal Hardware Runtime[/bold green] v{RUNTIME_VERSION}")
    console.print(
        f"Platform: {platform.system()} {platform.machine()} (Python {sys.version.split()[0]})"
    )

    console.print("\n[bold]Tools Check:[/bold]")
    tools = ["esptool", "arduino-cli", "openocd", "avrdude", "picotool"]
    for t in tools:
        path = shutil.which(t)
        if path:
            console.print(f"  [green]✓[/green] {t}: {path}")
        else:
            console.print(f"  [yellow]✗[/yellow] {t}: Not found in PATH")


if __name__ == "__main__":
    app()
