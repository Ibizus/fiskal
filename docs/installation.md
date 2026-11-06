# Installation

Fiskal is a Python application managed with [uv](https://docs.astral.sh/uv/).

## Requirements

- Python 3.12 or newer.
- [uv](https://docs.astral.sh/uv/getting-started/installation/).

## Steps

1. Clone the repository:

   ```bash
   git clone https://github.com/Ibizus/fiskal.git
   cd fiskal
   ```

2. Install dependencies and create the virtual environment:

   ```bash
   uv sync --all-extras
   ```

3. Verify the installation:

   ```bash
   uv run pytest
   ```

You are now ready to run Fiskal. See [Usage](usage.md) for next steps.
