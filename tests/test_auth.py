"""Access control regression tests.

Run from the repository root:  python3 -m unittest discover -s tests

These cover the two authorisation defects the product audit rated P0: an
empty token authorising everyone rather than nobody, and a reverse proxy on
the same host making every request look like loopback.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powermon import (  # noqa: E402
    authorize,
    hash_token,
    effective_client,
    in_networks,
    is_loopback,
    parse_trusted_proxies,
    validate_server_config,
)

TOKEN = "s3cret-token"


def keys_for(token=TOKEN):
    """The one-key shorthand, as load_keys() builds it from server.token."""
    if not token:
        return []
    return [{"name": "config-token", "scope": "admin",
             "hash": hash_token(token), "created": None}]


def allowed(**kw):
    return authorize(**kw)[0]


class IsLoopbackTests(unittest.TestCase):
    def test_loopback_forms(self):
        for addr in ("127.0.0.1", "127.0.0.5", "::1", "::ffff:127.0.0.1"):
            self.assertTrue(is_loopback(addr), addr)

    def test_non_loopback_forms(self):
        for addr in ("192.168.0.30", "100.82.182.116", "0.0.0.0", "::", "8.8.8.8"):
            self.assertFalse(is_loopback(addr), addr)

    def test_garbage_is_not_loopback(self):
        for addr in ("", "localhost", "not-an-ip", "127.0.0.1extra", None):
            self.assertFalse(is_loopback(addr), repr(addr))


class TrustedProxyParsingTests(unittest.TestCase):
    def test_empty_means_trust_nothing(self):
        self.assertEqual(parse_trusted_proxies(""), [])
        self.assertEqual(parse_trusted_proxies(None), [])

    def test_comma_separated_string_and_cidr(self):
        nets = parse_trusted_proxies("127.0.0.1, 172.17.0.0/16")
        self.assertTrue(in_networks("127.0.0.1", nets))
        self.assertTrue(in_networks("172.17.0.9", nets))
        self.assertFalse(in_networks("192.168.0.30", nets))

    def test_list_form_is_accepted(self):
        nets = parse_trusted_proxies(["::1", "10.0.0.0/8"])
        self.assertTrue(in_networks("::1", nets))
        self.assertTrue(in_networks("10.1.2.3", nets))

    def test_mixed_families_do_not_raise(self):
        nets = parse_trusted_proxies("10.0.0.0/8, fd00::/8")
        self.assertTrue(in_networks("10.0.0.1", nets))
        self.assertFalse(in_networks("192.168.1.1", nets))

    def test_bad_entry_is_loud_not_silent(self):
        # Silently dropping an entry would silently change who is trusted.
        with self.assertRaises(ValueError):
            parse_trusted_proxies("127.0.0.1, banana")


class EffectiveClientTests(unittest.TestCase):
    """The P0: a proxy on this host makes every request look like loopback."""

    def test_headers_from_an_untrusted_peer_are_ignored(self):
        # The spoof attempt: a LAN client claiming to be loopback.
        self.assertEqual(
            effective_client("192.168.0.30", "127.0.0.1", parse_trusted_proxies("")),
            "192.168.0.30",
        )

    def test_headers_ignored_even_when_peer_is_loopback_but_untrusted(self):
        # Loopback is not automatically a trusted proxy.
        self.assertEqual(
            effective_client("127.0.0.1", "8.8.8.8", parse_trusted_proxies("")),
            "127.0.0.1",
        )

    def test_trusted_proxy_forwards_the_real_client(self):
        trusted = parse_trusted_proxies("127.0.0.1")
        self.assertEqual(effective_client("127.0.0.1", "192.168.0.30", trusted),
                         "192.168.0.30")

    def test_rightmost_untrusted_hop_wins_in_a_chain(self):
        trusted = parse_trusted_proxies("127.0.0.1, 10.0.0.0/8")
        # client -> edge proxy -> local proxy -> powermon
        self.assertEqual(
            effective_client("127.0.0.1", "203.0.113.7, 10.0.0.5", trusted),
            "203.0.113.7",
        )

    def test_spoofed_prefix_from_a_real_client_cannot_win(self):
        trusted = parse_trusted_proxies("127.0.0.1")
        # The client injected a header of its own before the proxy appended.
        self.assertEqual(
            effective_client("127.0.0.1", "127.0.0.1, 192.168.0.30", trusted),
            "192.168.0.30",
        )

    def test_trusted_peer_without_a_header_falls_back_to_the_peer(self):
        trusted = parse_trusted_proxies("127.0.0.1")
        self.assertEqual(effective_client("127.0.0.1", None, trusted), "127.0.0.1")


class AuthorizeTests(unittest.TestCase):
    def test_loopback_needs_no_token(self):
        # Keeps `pwr` and SSH tunnels working with no configuration.
        self.assertTrue(allowed(client="127.0.0.1", token_supplied=None,
                                keys=keys_for()))

    def test_empty_token_denies_remote_instead_of_allowing_everyone(self):
        # The P0. Previously this returned True for every client on earth.
        self.assertFalse(allowed(client="192.168.0.30", token_supplied=None,
                                 keys=keys_for("")))
        self.assertFalse(allowed(client="192.168.0.30", token_supplied="anything",
                                 keys=keys_for("")))

    def test_remote_with_the_right_token(self):
        self.assertTrue(allowed(client="100.82.182.116", token_supplied=TOKEN,
                                keys=keys_for()))

    def test_remote_with_a_wrong_or_missing_token(self):
        self.assertFalse(allowed(client="100.82.182.116", token_supplied="nope",
                                 keys=keys_for()))
        self.assertFalse(allowed(client="100.82.182.116", token_supplied=None,
                                 keys=keys_for()))

    def test_non_ascii_token_does_not_raise(self):
        self.assertFalse(allowed(client="192.168.0.30", token_supplied="café",
                                 keys=keys_for()))

    def test_require_token_always_removes_the_loopback_exemption(self):
        self.assertFalse(allowed(client="127.0.0.1", token_supplied=None,
                                 keys=keys_for(), require_token_always=True))
        self.assertTrue(allowed(client="127.0.0.1", token_supplied=TOKEN,
                                keys=keys_for(), require_token_always=True))

    def test_the_matching_key_is_returned_so_it_can_be_logged(self):
        ok, key = authorize(client="10.0.0.9", token_supplied=TOKEN, keys=keys_for())
        self.assertTrue(ok)
        self.assertEqual(key["name"], "config-token")

    def test_loopback_is_admitted_without_a_key(self):
        ok, key = authorize(client="127.0.0.1", token_supplied=None, keys=keys_for())
        self.assertTrue(ok)
        self.assertIsNone(key)


class ValidateServerConfigTests(unittest.TestCase):
    @staticmethod
    def cfg(host, token, trusted=""):
        return {"server": {"host": host, "token": token, "trusted_proxies": trusted}}

    def test_refuses_to_start_when_exposed_without_a_token(self):
        for host in ("0.0.0.0", "", "::", "192.168.0.30", "100.82.182.116"):
            message = validate_server_config(self.cfg(host, ""))
            self.assertIsNotNone(message, host)
            self.assertIn("token", message)

    def test_loopback_without_a_token_is_fine(self):
        for host in ("127.0.0.1", "::1"):
            self.assertIsNone(validate_server_config(self.cfg(host, "")), host)

    def test_exposed_with_a_token_is_fine(self):
        self.assertIsNone(validate_server_config(self.cfg("0.0.0.0", TOKEN)))

    def test_unparseable_trusted_proxies_is_fatal(self):
        message = validate_server_config(self.cfg("127.0.0.1", "", "banana"))
        self.assertIsNotNone(message)
        self.assertIn("trusted_proxies", message)

    def test_message_names_the_config_file(self):
        message = validate_server_config(self.cfg("0.0.0.0", ""), Path("/etc/config.toml"))
        self.assertIn("/etc/config.toml", message)


if __name__ == "__main__":
    unittest.main()
