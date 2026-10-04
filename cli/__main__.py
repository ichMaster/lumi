"""`python -m cli <subcommand>` — see cli.main."""

import sys

from cli.main import main

if __name__ == "__main__":
    sys.exit(main())
