import runpy
import sys

namespace = runpy.run_path(f"{sys.argv[1]}/src/example.py")
raise SystemExit(0 if namespace["add"](2, 3) == 5 else 1)
