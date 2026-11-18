# Copyright 2026 Iván Moreno Mendoza
# SPDX-License-Identifier: Apache-2.0

import sys

from .cli import main

raise SystemExit(main(sys.argv[1:]))
