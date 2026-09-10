"""Emit the public OpenAPI contract without starting the server."""

import json
import sys

from celine.community.main import app


def main() -> None:
    json.dump(app.openapi(), sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
