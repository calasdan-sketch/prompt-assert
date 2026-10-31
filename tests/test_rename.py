"""The 0.2.0 rename: PROMPT_ASSERT_* settings with PROMPTREGRESS_* as a
fallback, and the old console script kept as an alias."""

import os
import unittest
from unittest import mock

from prompt_assert import model as model_mod


class TestEnvFallback(unittest.TestCase):
    def test_new_name_wins_over_old(self):
        env = {"PROMPT_ASSERT_MODEL": "new", "PROMPTREGRESS_MODEL": "old"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(model_mod.endpoint()[0], "new")

    def test_old_name_still_works(self):
        with mock.patch.dict(os.environ, {"PROMPTREGRESS_MODEL": "old"}):
            os.environ.pop("PROMPT_ASSERT_MODEL", None)
            self.assertEqual(model_mod.endpoint()[0], "old")

    def test_old_timeout_still_works(self):
        with mock.patch.dict(os.environ, {"PROMPTREGRESS_TIMEOUT": "7"}):
            os.environ.pop("PROMPT_ASSERT_TIMEOUT", None)
            self.assertEqual(model_mod.resolve_timeout(), 7)


if __name__ == "__main__":
    unittest.main()
