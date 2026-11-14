"""patch_factory_server (2026-11-14): /reset leaves the Terrier as the
server, the 41-byte field keeps its length, the file keeps its CRLF.

    python3 -m unittest discover -s firmware-arm/tests/apply_mods
"""

import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("apply_mods", HERE / "apply-mods.py")
am = importlib.util.module_from_spec(spec)
spec.loader.exec_module(am)
BOOT = HERE / "vendor/nabgcc/mtl/boot/boot.0.0.0.13.mtl"


def server_field(path: Path) -> str:
    t = open(path, newline="").read()
    a = t.index('const conf0=\r\n"') + len('const conf0=\r\n"')
    return t[a:t.index("\\\r\n", a)]


class FactoryServer(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "mtl/boot").mkdir(parents=True)
        shutil.copy(BOOT, self.root / "mtl/boot/boot.0.0.0.13.mtl")
        self.q = self.root / "mtl/boot/boot.0.0.0.13.mtl"

    def tearDown(self):
        shutil.rmtree(self.root)
        os.environ.pop("NAB_FACTORY_SERVER", None)

    def test_the_terrier_replaces_violet_in_the_same_41_bytes(self):
        before = server_field(BOOT)
        am.patch_factory_server(self.root)
        after = server_field(self.q)
        self.assertTrue(after.startswith("terrier.cyberloutre.fr/vl\\0"))
        self.assertEqual(len(after.replace("\\0", "\0")), len(before.replace("\\0", "\0")))
        self.assertEqual(len(after.replace("\\0", "\0")), 41)

    def test_only_that_line_changes_and_crlf_is_kept(self):
        old = open(BOOT, newline="").read()
        am.patch_factory_server(self.root)
        new = open(self.q, newline="").read()
        self.assertEqual(old.count("\r\n"), new.count("\r\n"))
        diff = [i for i, (a, b) in enumerate(zip(old.split("\r\n"), new.split("\r\n"))) if a != b]
        self.assertEqual(len(diff), 1)

    def test_it_runs_twice(self):
        am.patch_factory_server(self.root)
        once = self.q.read_bytes()
        am.patch_factory_server(self.root)
        self.assertEqual(once, self.q.read_bytes())

    def test_another_warren(self):
        os.environ["NAB_FACTORY_SERVER"] = "nabaztag.lan/vl"
        am.patch_factory_server(self.root)
        self.assertTrue(server_field(self.q).startswith("nabaztag.lan/vl\\0"))

    def test_a_name_too_long_is_refused(self):
        os.environ["NAB_FACTORY_SERVER"] = "x" * 41
        with self.assertRaises(SystemExit):
            am.patch_factory_server(self.root)


if __name__ == "__main__":
    unittest.main()
