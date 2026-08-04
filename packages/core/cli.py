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


def hardwaremcp_start() -> None:
    """Entry point for the 'hardwaremcp' global command."""
    import sys
    import platform
    import uvicorn
    import socket
    from rich.panel import Panel
    from rich.align import Align
    from packages.core.config import load_config, RUNTIME_VERSION

    cfg = load_config()
    host = cfg.server.host
    port = cfg.server.port
    
    if host == "0.0.0.0":
        display_host = "localhost"
        try:
            # Try to get the local network IP for convenience
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            network_ip = s.getsockname()[0]
            s.close()
            display_host = f"localhost (or {network_ip})"
        except Exception:
            pass
    else:
        display_host = host

    banner = f"""[bold green]UNIVERSAL HARDWARE RUNTIME[/bold green] v{RUNTIME_VERSION}
[dim]One Runtime. One API. Unlimited Hardware.[/dim]

[bold cyan]▶ Web Dashboard:[/bold cyan]    http://{display_host}:{port}/
[bold cyan]▶ REST API Docs:[/bold cyan]    http://{display_host}:{port}/docs
[bold cyan]▶ Health Check:[/bold cyan]     http://{display_host}:{port}/api/v1/system/health
[bold cyan]▶ MCP Config:[/bold cyan]       [dim]"command": "python", "args": ["-m", "packages.mcp_server.server"][/dim]

[bold yellow]System:[/bold yellow] {platform.system()} {platform.machine()} | Python {sys.version.split()[0]}
[bold yellow]API Key:[/bold yellow] {cfg.security.api_key}

[dim]Press [bold red]Ctrl+C[/bold red] to safely shut down the runtime.[/dim]"""

    console.print()
    console.print(Panel(Align.center(banner), border_style="green", expand=False))
    console.print()

    # Pass control to API factory
    from packages.core.api import create_app
    fastapi_app = create_app(cfg)

    # Uvicorn handles the SIGINT (Ctrl+C) and gracefully triggers the FastAPI lifespan shutdown
    uvicorn.run(
        fastapi_app,
        host=cfg.server.host,
        port=cfg.server.port,
        workers=cfg.server.workers,
        reload=cfg.server.reload and cfg.is_development,
        log_level=cfg.logging.level.lower(),
    )


if __name__ == "__main__":
    app()
