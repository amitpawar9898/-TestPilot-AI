import os
import sys
from pathlib import Path

os.environ["TESTPILOT_ALLOW_UNSAFE_LOCAL"] = "1"  # tests run their own trusted fixtures through the local runner
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
