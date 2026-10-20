"""Entry point for Blender: blender -b --factory-startup -P run_converter.py -- <args>"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vroid_kassandra import cli  # noqa: E402

code = cli.main()
sys.stdout.flush()
sys.stderr.flush()
# Leave right away: everything is written, and some Blender builds crash while
# shutting down after a background run, which would turn success into a failure.
os._exit(code)
