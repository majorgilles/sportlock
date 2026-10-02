# Keep tests away from the real data, state and log files: point the XDG dirs at a temp
# directory before any sportlock module computes its paths.
import os
import tempfile

_sandbox = tempfile.mkdtemp(prefix="sportlock-tests-")
for _var in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR"):
    os.environ[_var] = os.path.join(_sandbox, _var.lower())
    os.makedirs(os.environ[_var], exist_ok=True)
