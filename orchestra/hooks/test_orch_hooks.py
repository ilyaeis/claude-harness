"""Regression tests for orch_hooks.py rm-explainer. Run: python3 -m unittest test_orch_hooks (in this dir)."""
import json
import os
import tempfile
import unittest

import orch_hooks as h

HERE = os.path.dirname(os.path.abspath(__file__))
h.CONTRACT = os.path.join(HERE, "..", "contract.yaml")   # test the repo copy, not the installed one
CASES = [  # (command, bundled delete?)
    ("rm -rf /x", False),
    ("find . -name x -delete", False),
    ("ssh h 'rm -rf /x'", False),
    ("bash -c 'rm -rf /x'", False),
    ("mkdir a && cp b a && rm -rf c", True),
    ("ls | xargs rm", True),
    ("ls | xargs -0 rm -f", True),
    ("ssh h 'cd /x; rm -rf y'", True),
    ("ssh -o BatchMode=yes -p 22 h 'cd /x && rm -rf y'", True),
    ("bash -c 'cd /x && rm y'", True),
    ("find . -delete; ls", True),
    (r"find . -name '*.tmp' -exec rm {} \; ; ls", True),
    ("cd /x\nrm -rf y", True),
    ("sudo rm -rf a && ls", True),
    ("X=1 rm a; ls", True),
    ("if [ -d a ]; then rm -rf a; fi", True),
    ("echo rmx; ls", False),
    ("git status && git rm a", False),
    ("cat f | grep rm", False),
    ("echo 'rm -rf /' && ls", False),
    ("printf 'a && rm -rf b\\n' > f; cat f", False),
    ("python3 -c 'import os; os.system(\"rm -rf x\")' && ls", False),
    ("cat > f <<'EOF'\na && rm -rf b\nEOF\nls", False),
    ("ls 2>&1 | tail -3", False),
    ("x=\"$(printf \"a && rm -rf b\")\"; echo $x", False),
    ("du -sh \"$(pwd)\" && rm -rf a", True),
    ("echo 'unbalanced && rm x", False),   # unparsable -> fail open
]


class RmExplainer(unittest.TestCase):
    def test_cases(self):
        for cmd, want in CASES:
            with self.subTest(cmd=cmd):
                self.assertEqual(h.bundled_delete(cmd), want)

    @unittest.skipUnless(os.path.exists(os.path.join(HERE, "rm_cases.json")), "no rm_cases.json (local log data, not in the repo)")
    def test_logged_hits(self):
        """Real rm-explainer hits from hooks.log, saved in rm_cases.json: [{name, cmd, bundle}]."""
        with open(os.path.join(HERE, "rm_cases.json")) as f:
            cases = json.load(f)
        for c in cases:
            with self.subTest(c["name"]):
                self.assertEqual(h.bundled_delete(c["cmd"]), c["bundle"])


class Envelope(unittest.TestCase):
    def test_colon_after_to(self):
        """hooks.log 12:13:25 false hit: 'FIX t-28 master-harness -> harness-ac: ...' kept the colon."""
        m = h.ENVELOPE.match("FIX t-28 master-harness -> harness-ac: checked")
        self.assertEqual(m.groups(), ("FIX", "t-28", "master-harness", "harness-ac"))
        self.assertEqual(h.ENVELOPE.match("DONE t-28 harness-ac -> master-harness").group(4), "master-harness")
        self.assertIsNone(h.ENVELOPE.match("hello there"))

    def test_master_to_own_worker(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json") as reg:
            json.dump([{"name": "harness-zz", "status": "running"}], reg)
            reg.flush()
            old, h.REGISTRIES = h.REGISTRIES, reg.name
            try:
                self.assertIn("FIX", h.allowed_types("master-harness", "harness-zz"))
                self.assertIsNone(h.allowed_types("master-dev", "harness-zz"))
            finally:
                h.REGISTRIES = old


if __name__ == "__main__":
    unittest.main()
