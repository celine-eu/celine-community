"""Shared test setup.

The suite exercises the zero-config development defaults, which only
`CELINE_ENV=dev` accepts (celine.sdk.posture: unset is hardened). Pinned before
any test module imports `celine.community.settings`; the hardened posture has
its own tests in `test_posture.py`, which set the environment explicitly.
"""

import os

os.environ.setdefault("CELINE_ENV", "dev")
