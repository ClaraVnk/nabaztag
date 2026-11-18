"""The 2026-11-18 firmware review's steps and the mode table they live in.

    python3 -m unittest discover -s firmware-arm/tests/apply_mods

- vm_seed_hidden hides the bytes nab_rng.h says the seed occupies;
- naboot-1.2.0.3 and -tel differ by dcc_telemetry alone, the measurement
  modes by their one step, and every 1.2.0.1 variant is 1.2.0.1 plus known
  extras -- except kxprobe's missing owner_unlock, which its comment
  explains (review C-2: drift in 40 copied step lists is invisible);
- config_presence's bootloader edit: the save alone applies arguments, a
  new server asks for the head button, the page carries the hint.
"""

import importlib.util
import re
import shutil
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("apply_mods", HERE / "apply-mods.py")
am = importlib.util.module_from_spec(spec)
spec.loader.exec_module(am)
spec = importlib.util.spec_from_file_location("mode_diff", HERE / "tools/mode-diff.py")
md = importlib.util.module_from_spec(spec)
spec.loader.exec_module(md)


class SeedOffset(unittest.TestCase):
    def test_the_vm_hides_what_nab_rng_stores(self):
        h = (HERE / "crypto/nab_rng.h").read_text()
        off = int(re.search(r"#define NAB_RNG_SEED_OFF\s+(\d+)", h).group(1))
        ln = int(re.search(r"#define NAB_RNG_SEED_LEN\s+(\d+)", h).group(1))
        self.assertEqual(am.VM_SEED_OFF, off)
        self.assertEqual(off + ln, 4096, "the seed is the sector's last 32 bytes")


class ModeTable(unittest.TestCase):
    def setUp(self):
        self.modes = md.modes()

    def assertVariant(self, base, name, extras, missing=()):
        added, lacking, ordered = md.diff(self.modes[base], self.modes[name])
        self.assertEqual(added, list(extras), f"{name} adds to {base}")
        self.assertEqual(lacking, list(missing), f"{name} lacks from {base}")
        self.assertTrue(ordered, f"{name} reorders {base}'s steps")

    def test_1_2_0_3_variants(self):
        self.assertVariant("naboot-1.2.0.3", "naboot-1.2.0.3-tel", ["dcc_telemetry"])
        self.assertVariant("naboot-1.2.0.3", "naboot-1.2.0.3-oscrypto", ["opt_size_crypto"])
        self.assertVariant("naboot-1.2.0.3", "naboot-1.2.0.3-chacha", ["tls_chacha_only"])

    def test_1_2_0_3_is_1_2_0_1_plus_the_review(self):
        self.assertVariant("naboot-1.2.0.1", "naboot-1.2.0.3", [
            "factory_server", "ie_walk_bounds", "ccmp_no_probation", "vm_strcmp_bound",
            "vm_seed_hidden", "tls_in_overflow", "gzip_pages", "gzip_setup", "config_presence",
            "boot_strip_wav", "boot_strip_login", "prune_unreachable"])

    def test_1_2_0_1_variants(self):
        self.assertVariant("naboot-1.2.0.1", "naboot-1.2.0.1-gz", ["gzip_pages", "gzip_setup", "prune_unreachable"])
        self.assertVariant("naboot-1.2.0.1", "naboot-1.2.0.1-gzrf", ["gzip_pages", "gzip_setup", "prune_unreachable", "rf2528_only"])
        # The one documented omission: "The test image leaves owner_unlock out
        # to fit (32 bytes over, 2026-11-13)."
        self.assertVariant("naboot-1.2.0.1", "naboot-1.2.0.1-kxprobe", ["kx_probe"], missing=["owner_unlock"])

    def test_the_review_steps_are_in_both_release_modes(self):
        for mode in ("naboot-1.2.0.3", "naboot-1.2.0.3-tel"):
            for step in ("ie_walk_bounds", "ccmp_no_probation", "vm_strcmp_bound", "vm_seed_hidden",
                         "tls_in_overflow", "config_presence", "boot_strip_wav", "boot_strip_login"):
                self.assertIn(step, self.modes[mode])
            for step in ("opt_size_crypto", "tls_chacha_only", "opt_size", "opt_size_safe", "opt_size_usb_o1"):
                self.assertNotIn(step, self.modes[mode], f"{step} is a measurement step")


class ConfigPresence(unittest.TestCase):
    """On a bootloader as gzip_setup leaves it: a small stand-in with the
    three anchors, not the whole pipeline."""

    def setUp(self):
        import gzip
        self.root = Path(tempfile.mkdtemp())
        (self.root / "mtl/boot").mkdir(parents=True)
        page = (b"<html><label><small>Boot URL host &mdash; e.g. <b>nabaztag.lan/vl</b></small>"
                b"<input name=a></label></html>")
        lit = am._metal_bytes_literal(b"html\r\n\r\n" + gzip.compress(page, 9, mtime=0))
        self.boot = self.root / "mtl/boot/boot.0.0.0.13.mtl"
        self.boot.write_bytes((
            "fun jscan s l=nil;;\r\n"
            "fun webserver s=s;;\r\n"
            f"fun page_a=strcat http_gzip {lit};;\r\n"
            "fun cbhttp req=\r\n"
            "\tlet uriextract uri -> [page args] in\r\n"
            "\t(\r\n"
            "\t\tnil; page;\r\n"
            "\t\tupdateconf args;\r\n"
            "\t\tif page==1 then httpdone\r\n"
            "\t\telse if page==2 then httpflash req\r\n"
            "\t);;\r\n").encode("latin-1"))

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_the_save_alone_applies_arguments_and_a_new_server_asks_for_the_head(self):
        import gzip
        am.patch_config_presence(self.root)
        t = self.boot.read_bytes().decode("latin-1")
        self.assertIn("\r\n", t, "the file keeps its CRLF")
        t = t.replace("\r\n", "\n")
        self.assertNotIn("\t\tupdateconf args;\n\t\tif page==1", t)
        self.assertIn("if page==1 then (if needhead args then page_error else (updateconf args; httpdone))", t)
        self.assertIn("if n=='a' && (strcmp v (webserver confGetServerUrl)) && !button2 then 1", t)
        # Metal's `if` jumps on 0 only (OPelse: `if (!VPULL())`), so nil is
        # true: needhead must answer an int both ways (2026-11-18, the plain
        # GET /b.htm answered the error page).
        self.assertIn("\tif args==nil then 0\n", t)
        lit = re.search(r'fun page_a=strcat http_gzip (".*?");;', t).group(1)
        raw = am._metal_literal_bytes(lit)
        page = gzip.decompress(raw.split(b"\r\n\r\n", 1)[1])
        self.assertIn(b"hold the rabbit's head while you press Connect", page)
        # idempotent
        am.patch_config_presence(self.root)
        self.assertEqual(t, self.boot.read_bytes().decode("latin-1").replace("\r\n", "\n"))

    def test_literal_round_trip(self):
        raw = bytes(range(256))
        self.assertEqual(am._metal_literal_bytes(am._metal_bytes_literal(raw)), raw)


if __name__ == "__main__":
    unittest.main()
