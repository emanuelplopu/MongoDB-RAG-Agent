"""Module entrypoint so ``python -m backend.cli`` invokes the CLI."""
from backend.cli.main import cli_entry

if __name__ == "__main__":
    cli_entry()
