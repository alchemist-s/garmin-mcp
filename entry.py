"""Entry point for the MCP Bundle build.

The host runs this as a file, not as a module, so relative imports inside the
package would fail. Putting ``src`` on the path first lets it import normally
whether or not the package itself was installed into the environment.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

from zonetwo.cli import main  # noqa: E402

raise SystemExit(main(["serve"]))
