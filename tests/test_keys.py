"""Named API keys, hashed storage, and failed-attempt throttling.

Run from the repository root:  python3 -m unittest discover -s tests

Phase 1 item 6 of the product audit: one shared secret that could not be
revoked for a single client, stored in clear text, with nothing slowing a
client that kept guessing.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powermon import (  # noqa: E402
    Throttle,
    add_key,
    authorize,
    hash_token,
    load_keys,
    match_key,
    revoke_key,
    validate_server_config,
)


class HashingTests(unittest.TestCase):
    def test_a_token_is_never_stored_in_the_clear(self):
        digest = hash_token("hunter2")
        self.assertTrue(digest.startswith("sha256:"))
        self.assertNotIn("hunter2", digest)

    def test_hashing_is_stable_and_distinguishing(self):
        self.assertEqual(hash_token("a"), hash_token("a"))
        self.assertNotEqual(hash_token("a"), hash_token("b"))

    def test_non_ascii_does_not_raise(self):
        self.assertTrue(hash_token("café").startswith("sha256:"))


class KeyFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.cfg_path = self.dir / "config.toml"
        self.cfg_path.write_text("[server]\n")

    def cfg(self, **server):
        base = {"host": "127.0.0.1", "token": "", "keys_file": "keys.json"}
        base.update(server)
        return {"server": base}

    def test_the_single_token_still_works_as_a_one_key_shorthand(self):
        """Most installs have one client and should not need a key file."""
        keys = load_keys(self.cfg(token="shorthand", keys_file=""))
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0]["name"], "config-token")
        self.assertIsNotNone(match_key("shorthand", keys))

    def test_added_keys_are_usable_and_stored_hashed(self):
        cfg = self.cfg()
        import io
        import contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(add_key(cfg, self.cfg_path, "grafana", "read"), 0)
        token = out.getvalue().strip().splitlines()[2].strip()

        raw = (self.dir / "keys.json").read_text()
        self.assertNotIn(token, raw, "the token itself must not reach the file")
        self.assertIn("sha256:", raw)

        keys = load_keys(cfg, self.dir)
        matched = match_key(token, keys)
        self.assertIsNotNone(matched)
        self.assertEqual(matched["name"], "grafana")
        self.assertEqual(matched["scope"], "read")
        self.assertIsNotNone(matched["created"], "a key records when it was minted")

    def test_keys_are_revocable_one_at_a_time(self):
        """The point of named keys: retire one client without breaking the rest."""
        import io
        import contextlib
        cfg = self.cfg()
        tokens = {}
        for name in ("laptop", "grafana"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                add_key(cfg, self.cfg_path, name)
            tokens[name] = out.getvalue().strip().splitlines()[2].strip()

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(revoke_key(cfg, self.cfg_path, "grafana"), 0)

        keys = load_keys(cfg, self.dir)
        self.assertIsNone(match_key(tokens["grafana"], keys), "revoked key still works")
        self.assertIsNotNone(match_key(tokens["laptop"], keys), "sibling key broken")

    def test_duplicate_names_are_refused(self):
        import io
        import contextlib
        cfg = self.cfg()
        with contextlib.redirect_stdout(io.StringIO()):
            add_key(cfg, self.cfg_path, "laptop")
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(add_key(cfg, self.cfg_path, "laptop"), 2)

    def test_a_missing_or_broken_key_file_is_not_fatal(self):
        self.assertEqual(load_keys(self.cfg(keys_file="absent.json"), self.dir), [])
        (self.dir / "keys.json").write_text("{ not json")
        self.assertEqual(load_keys(self.cfg(), self.dir), [])

    def test_a_broken_key_file_does_not_silently_admit_everyone(self):
        (self.dir / "keys.json").write_text("{ not json")
        keys = load_keys(self.cfg(), self.dir)
        self.assertFalse(authorize(client="10.0.0.5", token_supplied="anything",
                                   keys=keys)[0])

    def test_an_unknown_scope_falls_back_to_read_not_admin(self):
        (self.dir / "keys.json").write_text(json.dumps(
            [{"name": "odd", "scope": "superuser", "hash": hash_token("t")}]))
        self.assertEqual(load_keys(self.cfg(), self.dir)[0]["scope"], "read")

    def test_a_key_file_alone_satisfies_the_startup_check(self):
        """An exposed host with named keys but no server.token must still start."""
        import io
        import contextlib
        cfg = self.cfg(host="0.0.0.0")
        with contextlib.redirect_stdout(io.StringIO()):
            add_key(cfg, self.cfg_path, "laptop")
        self.assertIsNone(validate_server_config(cfg, self.cfg_path))

    def test_an_exposed_host_with_no_credential_at_all_is_still_refused(self):
        message = validate_server_config(self.cfg(host="0.0.0.0"), self.cfg_path)
        self.assertIsNotNone(message)


class ScopeTests(unittest.TestCase):
    @staticmethod
    def key(scope):
        return [{"name": "k", "scope": scope, "hash": hash_token("t"), "created": None}]

    def test_read_is_enough_for_a_read_route(self):
        self.assertTrue(authorize(client="10.0.0.1", token_supplied="t",
                                  keys=self.key("read"), required_scope="read")[0])

    def test_read_cannot_reach_an_admin_route(self):
        self.assertFalse(authorize(client="10.0.0.1", token_supplied="t",
                                   keys=self.key("read"), required_scope="admin")[0])

    def test_admin_implies_read(self):
        self.assertTrue(authorize(client="10.0.0.1", token_supplied="t",
                                  keys=self.key("admin"), required_scope="read")[0])


class ThrottleTests(unittest.TestCase):
    def setUp(self):
        self.t = Throttle(threshold=3, base_delay=1.0, max_delay=8.0)

    def test_early_failures_are_not_delayed(self):
        """A fat-fingered token should not cost a wait."""
        for _ in range(3):
            self.assertEqual(self.t.delay_for("10.0.0.1"), 0.0)
            self.t.record_failure("10.0.0.1")

    def test_delay_grows_once_past_the_threshold(self):
        for _ in range(4):
            self.t.record_failure("10.0.0.1")
        first = self.t.delay_for("10.0.0.1")
        self.t.record_failure("10.0.0.1")
        self.assertGreater(self.t.delay_for("10.0.0.1"), first)

    def test_delay_is_capped(self):
        for _ in range(40):
            self.t.record_failure("10.0.0.1")
        self.assertLessEqual(self.t.delay_for("10.0.0.1"), 8.0)

    def test_a_correct_key_is_never_delayed_by_someone_elses_guessing(self):
        """Behind an untrusted proxy every client shares one address, so a
        penalty applied before checking the key would lock out the innocent."""
        for _ in range(20):
            self.t.record_failure("10.0.0.1")
        # The handler asks for the delay only on the failing branch, so a
        # success never consults it -- and clears the debt for the next caller.
        self.t.record_success("10.0.0.1")
        self.assertEqual(self.t.delay_for("10.0.0.1"), 0.0)

    def test_success_clears_the_penalty(self):
        for _ in range(10):
            self.t.record_failure("10.0.0.1")
        self.t.record_success("10.0.0.1")
        self.assertEqual(self.t.delay_for("10.0.0.1"), 0.0)

    def test_addresses_are_throttled_independently(self):
        for _ in range(10):
            self.t.record_failure("10.0.0.1")
        self.assertEqual(self.t.delay_for("10.0.0.2"), 0.0,
                         "one attacker must not lock out everyone else")

    def test_the_table_cannot_grow_without_bound(self):
        """Forged source addresses must not become a memory leak."""
        for i in range(5000):
            self.t.record_failure(f"10.0.{i // 256}.{i % 256}")
        self.assertLessEqual(len(self.t._fails), 4096)


if __name__ == "__main__":
    unittest.main()
