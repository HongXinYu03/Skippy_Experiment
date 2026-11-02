"""Keep `python -m skippy` as the offline algorithm entry point."""
from .algorithm.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
