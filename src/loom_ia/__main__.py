# SPDX-License-Identifier: Apache-2.0
"""``python -m loom_ia`` : le même programme que la commande ``loom``."""

import sys

from loom_ia.access.cli import main

if __name__ == "__main__":
    sys.exit(main())
