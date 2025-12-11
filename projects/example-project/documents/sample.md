# Example Project Document

This is a sample document for the example project profile.

## About This Project

This demonstrates how to use the profile feature to separate different projects.

## Key Features

- Each project has its own document folder
- Each project uses a separate MongoDB database
- Easy switching between projects via CLI commands

## Usage

1. Add your documents to `projects/example-project/documents/`
2. Switch to this profile: `switch example-project`
3. Run ingestion: `uv run python -m src.ingestion.ingest`
4. Start querying!
