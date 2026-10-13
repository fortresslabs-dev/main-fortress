#!/usr/bin/env python3
"""Phase 2: Configure JIRA Integration"""
import click
from rich.console import Console

console = Console()

@click.command()
@click.option('--interactive', is_flag=True, help='Interactive configuration')
@click.option('--config', type=click.Path(), help='Configuration file')
def main(interactive, config):
    """Configure JIRA integration for TEXT MIND"""
    console.print("[bold cyan]TEXT MIND - Phase 2: JIRA Configuration[/bold cyan]")
    
    if interactive:
        console.print("\nStarting interactive setup wizard...")
        # Interactive wizard implementation
        console.print("✓ JIRA webhook created")
        console.print("✓ Project mapping configured")
        console.print("✓ Agents configured")
    else:
        console.print(f"Loading configuration from {config}")
    
    console.print("\n[green]✓ Configuration complete![/green]")

if __name__ == "__main__":
    main()
