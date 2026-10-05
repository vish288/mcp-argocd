"""MCP server for the Argo CD API."""

import asyncio
import logging
import os

import click
from dotenv import load_dotenv


@click.command()
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
    default="stdio",
    help="MCP transport type (sse is deprecated; use streamable-http)",
)
@click.option("--port", default=8000, help="Port for HTTP transports")
@click.option("--host", default="127.0.0.1", help="Host for HTTP transports")
@click.option("--argocd-url", envvar="ARGOCD_URL", help="Argo CD base URL")
@click.option("--argocd-token", envvar="ARGOCD_TOKEN", help="Argo CD bearer token")
@click.option("--read-only", is_flag=True, help="Disable write operations")
@click.option("--insecure", is_flag=True, help="Skip TLS verification (ARGOCD_SSL_VERIFY=false)")
def main(
    transport: str,
    port: int,
    host: str,
    argocd_url: str | None,
    argocd_token: str | None,
    read_only: bool,
    insecure: bool,
) -> None:
    """Run the Argo CD MCP server."""
    load_dotenv()

    if argocd_url:
        os.environ["ARGOCD_URL"] = argocd_url
    if argocd_token:
        os.environ["ARGOCD_TOKEN"] = argocd_token
    if read_only:
        os.environ["ARGOCD_READ_ONLY"] = "true"
    if insecure:
        os.environ["ARGOCD_SSL_VERIFY"] = "false"

    if transport == "sse":
        click.echo(
            "Warning: --transport sse uses the HTTP+SSE transport, deprecated in MCP 2026-07-28. "
            "Use --transport streamable-http.",
            err=True,
        )

    logging.basicConfig(
        level=logging.INFO,
        format="%(name)s | %(message)s",
    )

    from .servers import prompts, resources  # noqa: F401 — registers decorators
    from .servers.argocd import mcp

    run_kwargs: dict = {"transport": transport}
    if transport != "stdio":
        run_kwargs["host"] = host
        run_kwargs["port"] = port

    asyncio.run(mcp.run_async(show_banner=False, **run_kwargs))


if __name__ == "__main__":
    main()
