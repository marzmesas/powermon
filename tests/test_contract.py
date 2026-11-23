"""Reading the price from a contract: redaction, the model's reply, the
arithmetic, the LLM client and the route guards.

Run from the repository root:  python3 -m unittest discover -s tests
No real model is contacted: the client is tested against a fake
OpenAI-compatible server on 127.0.0.1. All personal data here is fictional.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import powermon  # noqa: E402
from powermon import (  # noqa: E402
    ANALYSE_TIMES, ContractError, Handler, _parse_schedule, analyse_allowed,
    analyse_contract, build_policies, contract_proposal, extract_pdf_text, hash_token,
    llm_complete, llm_config, load_config, parse_model_json, redact_contract, settings_payload,
    write_refusal,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample-contract.pdf"
KEY = "sk-test-not-a-real-key-123"


def redacted(text):
    return redact_contract(text)[0]


class RedactionTests(unittest.TestCase):
    def test_iban_with_and_without_spaces(self):
        out, counts = redact_contract("IBAN: DE89 3704 0044 0532 0130 00 BIC: COBADEFFXXX\n"
                                      "DE89370400440532013000\nGB82 WEST 1234 5698 7654 32")
        self.assertNotIn("3704", out)
        self.assertNotIn("WEST", out)
        self.assertNotIn("COBADEFF", out)
        self.assertEqual(counts["iban"], 3)
        self.assertEqual(counts["bic"], 1)
        self.assertIn("BIC: [BIC]", out)   # the word after the IBAN is not eaten

    def test_labelled_iban_with_a_typo_is_still_removed(self):
        self.assertEqual(redacted("IBAN DE00 3704 0044 0532 0130 00"), "IBAN [IBAN]")

    def test_email_and_phone(self):
        out, counts = redact_contract("Mail kunde@example.com, Tel.: +49 30 1234 5678, "
                                      "Telefon 030 / 123 456-78, Hotline +44 20 7946 0000")
        self.assertEqual(out, "Mail [EMAIL], Tel.: [PHONE], Telefon [PHONE], Hotline [PHONE]")
        self.assertEqual((counts["email"], counts["phone"]), (1, 3))

    def test_customer_and_contract_numbers(self):
        out, counts = redact_contract("Kundennummer 4471 2290 16\nVertrag 1234-5678-90\n"
                                      "Zähler 1ESY1160123456")
        self.assertEqual(out, "Kundennummer [NUMBER]\nVertrag [NUMBER]\nZähler 1ESY[NUMBER]")
        self.assertEqual(counts["number"], 3)

    def test_prices_dates_and_years_survive(self):
        for line in ("Arbeitspreis 32,50 ct/kWh brutto", "0.325 €/kWh", "12,90 € / Monat",
                     "inkl. 19 % MwSt.", "Vertragsbeginn 01.03.2026", "ab 2026-03-01",
                     "im Jahr 2026", "€ 1.234,56", "HT 06:00-22:00", "250,00 EUR/MWh",
                     "Verbrauch 12345678 kWh", "Grundpreis 154,80 EUR pro Jahr",
                     "0,28745 EUR/kWh", "Postleitzahl 00000", "12.5 p/kWh", "$0.1834 per kWh"):
            out, counts = redact_contract(line)
            self.assertEqual(out, line)
            self.assertEqual(sum(counts.values()), 0, line)

    def test_layout_padding_is_squeezed_and_nul_removed(self):
        self.assertEqual(redacted("A        32,50 ct/kWh   \n\n\n\nB\x00"),
                         "A  32,50 ct/kWh\n\nB")


class ReplyParsingTests(unittest.TestCase):
    def test_plain_fenced_and_prose(self):
        for reply in ('{"a": 1}', '```json\n{"a": 1}\n```',
                      'Here you go:\n{"a": 1}\nHope that helps {really}.'):
            self.assertEqual(parse_model_json(reply), {"a": 1}, reply)
        self.assertEqual(parse_model_json('{"q": "a } inside \\" a string", "n": {"x": 2}}'),
                         {"q": 'a } inside " a string', "n": {"x": 2}})

    def test_garbage(self):
        for reply in ("", "no json here", "{not: json}", "[1, 2]", '{"a": 1'):
            with self.assertRaises(ValueError, msg=reply):
                parse_model_json(reply)


GERMAN = """Stromliefervertrag
Arbeitspreis 32,50 ct/kWh brutto
Grundpreis 12,90 €/Monat brutto
Alle Preise inkl. 19 % MwSt.
"""


def fact(value, unit, quote, vat=True, **extra):
    return {"value": value, "unit": unit, "includes_vat": vat, "quote": quote, **extra}


class ProposalTests(unittest.TestCase):
    def propose(self, facts, text=GERMAN):
        proposal, evidence, warnings = contract_proposal(facts, text)
        return proposal["tariff"], evidence, warnings

    def test_german_gross_flat(self):
        t, evidence, warnings = self.propose({
            "currency": "EUR", "contract_type": "flat",
            "energy_price": fact(32.5, "ct/kWh", "Arbeitspreis 32,50 ct/kWh brutto"),
            "standing_charge": fact(12.9, "EUR", "12,90 €/Monat", period="month"),
            "vat_pct": {"value": 19, "quote": "inkl. 19 % MwSt."}})
        self.assertEqual(t["rate"], 0.325)
        self.assertAlmostEqual(t["standing_charge_per_day"], 12.9 * 12 / 365, places=5)
        self.assertEqual(t["tax_pct"], 0)
        self.assertEqual((t["mode"], t["currency"], t["symbol"]), ("flat", "EUR", "€"))
        self.assertEqual(warnings, [])
        rate = next(e for e in evidence if e["key"] == "tariff.rate")
        self.assertEqual(rate["quote"], "Arbeitspreis 32,50 ct/kWh brutto")

    def test_net_prices_with_a_stated_rate(self):
        text = "Energy 0.2500 GBP/kWh excl. VAT. Standing charge 0.45 GBP per day excl. VAT. VAT 5%."
        t, _, warnings = self.propose({
            "currency": "GBP", "contract_type": "flat",
            "energy_price": fact(0.25, "GBP/kWh", "Energy 0.2500 GBP/kWh", vat=False),
            "standing_charge": fact(0.45, "GBP", "0.45 GBP per day", vat=False, period="day"),
            "vat_pct": {"value": 5, "quote": "VAT 5%"}}, text)
        self.assertEqual((t["rate"], t["standing_charge_per_day"], t["tax_pct"]), (0.25, 0.45, 5))
        self.assertEqual(t["symbol"], "£")
        self.assertEqual(warnings, [])

    def test_net_prices_without_a_rate_warn_loudly_and_leave_tax_alone(self):
        text = "Arbeitspreis 27,31 ct/kWh netto"
        t, _, warnings = self.propose({"contract_type": "flat", "energy_price":
                                       fact(27.31, "ct/kWh", "27,31 ct/kWh netto", vat=False)}, text)
        self.assertEqual(t["rate"], 0.2731)
        self.assertNotIn("tax_pct", t)
        self.assertTrue(any("BEFORE VAT" in w for w in warnings))

    def test_mixed_gross_and_net_become_gross(self):
        text = "Arbeitspreis 32,50 ct/kWh brutto. Grundpreis 10,00 EUR/Monat netto. MwSt. 19 %"
        t, _, warnings = self.propose({
            "contract_type": "flat",
            "energy_price": fact(32.5, "ct/kWh", "32,50 ct/kWh brutto"),
            "standing_charge": fact(10, "EUR", "Grundpreis 10,00 EUR/Monat netto", vat=False,
                                    period="month"),
            "vat_pct": {"value": 19, "quote": "MwSt. 19 %"}}, text)
        self.assertEqual(t["rate"], 0.325)
        self.assertAlmostEqual(t["standing_charge_per_day"], 10 * 1.19 * 12 / 365, places=5)
        self.assertEqual(t["tax_pct"], 0)
        self.assertTrue(any("raised by 19 %" in w for w in warnings))

    def test_two_band_time_of_use(self):
        text = "HT Mo-Fr 06:00-22:00 34,10 ct/kWh\nNT 22:00-06:00 25,00 ct/kWh\nWochenende 25,00 ct/kWh"
        t, evidence, warnings = self.propose({"contract_type": "time_of_use", "currency": "EUR",
            "time_of_use": [
                fact(34.1, "ct/kWh", "06:00-22:00 34,10 ct/kWh", days="weekday", **{"from": "06:00", "to": "22:00"}),
                fact(25, "ct/kWh", "NT 22:00-06:00 25,00 ct/kWh", days="weekday", **{"from": "22:00", "to": "06:00"}),
                fact(25, "ct/kWh", "Wochenende 25,00 ct/kWh", days="weekend", **{"from": "00:00", "to": "24:00"})]},
            text)
        self.assertEqual(t["mode"], "tou")
        self.assertEqual(_parse_schedule(t["tou_weekday"]),
                         [(0, 6, 0.25), (6, 22, 0.341), (22, 24, 0.25)])
        self.assertEqual(_parse_schedule(t["tou_weekend"]), [(0, 24, 0.25)])
        self.assertEqual(len([e for e in evidence if e["key"].startswith("tariff.tou_")]), 3)
        self.assertNotIn("not cover", " ".join(warnings))
        # And the whole tariff it proposes is one Tariff accepts.
        cfg = load_config(Path("/nonexistent.toml"), overrides=False)
        cfg["tariff"].update(t)
        build_policies(cfg, None)

    def test_incomplete_time_of_use_falls_back_to_flat(self):
        text = "Arbeitspreis 30,00 ct/kWh. HT 08:00-20:00 34,10 ct/kWh"
        t, _, warnings = self.propose({"contract_type": "time_of_use",
            "energy_price": fact(30, "ct/kWh", "Arbeitspreis 30,00 ct/kWh"),
            "time_of_use": [fact(34.1, "ct/kWh", "08:00-20:00 34,10 ct/kWh", days="all",
                                 **{"from": "08:00", "to": "20:00"})]}, text)
        self.assertEqual((t["mode"], t["rate"]), ("flat", 0.3))
        self.assertNotIn("tou_weekday", t)
        self.assertTrue(any("24 hours" in w for w in warnings))

    def test_dynamic_proposes_surcharge_only(self):
        text = "Börsenpreis zzgl. 18,20 ct/kWh Aufschlag brutto. Grundpreis 9,90 € pro Monat. Beispiel 30 ct/kWh"
        t, _, warnings = self.propose({"contract_type": "dynamic",
            "energy_price": fact(30, "ct/kWh", "Beispiel 30 ct/kWh"),
            "dynamic_fees_per_kwh": fact(18.2, "ct/kWh", "18,20 ct/kWh Aufschlag"),
            "standing_charge": fact(9.9, "€", "9,90 € pro Monat", period="month")}, text)
        self.assertEqual(t["dynamic_surcharge"], 0.182)
        self.assertIn("standing_charge_per_day", t)
        self.assertEqual(t["tax_pct"], 0)
        for key in ("mode", "rate", "dynamic_source", "dynamic_zone"):
            self.assertNotIn(key, t)
        self.assertTrue(any("price source" in w for w in warnings))

    def test_per_year_standing_and_per_mwh_price(self):
        text = "Energy 250,00 EUR/MWh incl. VAT; standing charge 146,00 EUR per year incl. VAT"
        t, _, _ = self.propose({"contract_type": "flat",
            "energy_price": fact(250, "EUR/MWh", "250,00 EUR/MWh"),
            "standing_charge": fact(146, "EUR", "146,00 EUR per year", period="year")}, text)
        self.assertEqual(t["rate"], 0.25)
        self.assertEqual(t["standing_charge_per_day"], 0.4)

    def test_out_of_range_values_are_dropped(self):
        text = "Preis 650 ct/kWh. Grundpreis 900 EUR pro Monat. MwSt. 55 %"
        t, _, warnings = self.propose({"contract_type": "flat",
            "energy_price": fact(650, "ct/kWh", "650 ct/kWh"),
            "standing_charge": fact(900, "EUR", "900 EUR pro Monat", period="month"),
            "vat_pct": {"value": 55, "quote": "MwSt. 55 %"}}, text)
        for key in ("rate", "standing_charge_per_day", "mode"):
            self.assertNotIn(key, t)
        self.assertEqual(len([w for w in warnings if w.startswith("Dropped")]), 3)

    def test_a_quote_not_in_the_text_drops_the_value(self):
        t, _, warnings = self.propose({"contract_type": "flat",
            "energy_price": fact(29.9, "ct/kWh", "Arbeitspreis 29,90 ct/kWh")})
        self.assertNotIn("rate", t)
        self.assertTrue(any("not in the document" in w for w in warnings))

    def test_a_value_its_quote_does_not_contain_is_dropped(self):
        t, _, warnings = self.propose({"contract_type": "flat",
            "energy_price": fact(0.325, "EUR/kWh", "Arbeitspreis 32,50 ct/kWh")})
        self.assertNotIn("rate", t)
        self.assertTrue(any("does not appear" in w for w in warnings))

    def test_quote_matching_ignores_case_and_whitespace(self):
        t, _, _ = self.propose({"contract_type": "flat",
            "energy_price": fact(32.5, "ct/kWh", "ARBEITSPREIS   32,50\nct/kWh")})
        self.assertEqual(t["rate"], 0.325)

    def test_junk_from_the_model_proposes_nothing(self):
        for facts in ({}, [], {"energy_price": "32,5"}, {"energy_price": {"value": True}},
                      {"energy_price": fact(float("nan"), "ct/kWh", "32,50")}):
            t, _, warnings = self.propose(facts)
            self.assertEqual(t, {}, facts)
            self.assertTrue(warnings)


# --------------------------------------------------------------------------- fake model server


class FakeLLM(BaseHTTPRequestHandler):
    seen: list = []
    reply = "{}"
    status = 200
    delay = 0.0

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).seen.append({"path": self.path, "auth": self.headers.get("Authorization"),
                                "body": body})
        time.sleep(self.delay)
        out = json.dumps({"choices": [{"message": {"role": "assistant",
                                                    "content": self.reply}}]}).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class LlmClientTests(unittest.TestCase):
    def setUp(self):
        self.H = type("H", (FakeLLM,), {"seen": [], "reply": "{}", "status": 200, "delay": 0.0})
        self.server = serve(self.H)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def llm(self, key=KEY, timeout=5.0):
        return llm_config({"llm": {"base_url": self.base + "/", "api_key": key,
                                   "model": "test/model", "timeout": timeout}})

    def test_request_shape_and_bearer_only_with_a_key(self):
        self.H.reply = "hello"
        self.assertEqual(llm_complete(self.llm(), "sys", "user"), "hello")
        self.assertEqual(llm_complete(self.llm(key=""), "sys", "user"), "hello")
        with_key, without = self.H.seen
        self.assertEqual(with_key["path"], "/v1/chat/completions")
        self.assertEqual(with_key["auth"], f"Bearer {KEY}")
        self.assertIsNone(without["auth"])
        body = with_key["body"]
        self.assertEqual(body["model"], "test/model")
        self.assertEqual((body["temperature"], body["max_tokens"]), (0, 1200))
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])
        self.assertNotIn("response_format", body)

    def test_rate_limited_and_timeout_are_clean_errors_without_the_key(self):
        self.H.status = 429
        with self.assertRaises(ContractError) as ctx:
            llm_complete(self.llm(), "s", "u")
        self.assertEqual(ctx.exception.status, 502)
        self.assertIn("429", str(ctx.exception))
        self.assertNotIn(KEY, str(ctx.exception))
        self.H.status, self.H.delay = 200, 1.0
        with self.assertRaises(ContractError) as ctx:
            llm_complete(self.llm(timeout=0.2), "s", "u")
        self.assertEqual(ctx.exception.status, 504)
        self.assertNotIn(KEY, str(ctx.exception))

    def test_analyse_end_to_end_and_unparseable_reply(self):
        self.H.reply = "```json\n" + json.dumps({
            "currency": "EUR", "contract_type": "flat",
            "energy_price": fact(32.5, "ct/kWh", "Arbeitspreis 32,50 ct/kWh brutto"),
            "notes": "Looks complete."}) + "\n```"
        cfg = {"llm": {"base_url": self.base, "api_key": KEY, "model": "test/model"}}
        out = analyse_contract(cfg, GERMAN)
        self.assertEqual(out["proposal"]["tariff"]["rate"], 0.325)
        self.assertEqual((out["model"], out["host"]), ("test/model", "127.0.0.1"))
        self.assertNotIn(KEY, json.dumps(out))
        sent = self.H.seen[-1]["body"]["messages"]
        self.assertIn("ignore", sent[0]["content"].lower())
        self.assertIn("Arbeitspreis 32,50", sent[1]["content"])
        self.H.reply = "Sorry, I cannot help with that. " * 30
        with self.assertRaises(ContractError) as ctx:
            analyse_contract(cfg, GERMAN)
        self.assertEqual(ctx.exception.status, 502)
        self.assertLess(len(str(ctx.exception)), 400)

    def test_config_validation(self):
        for bad in ({"base_url": "file:///etc/passwd"}, {"base_url": "ftp://x"},
                    {"base_url": "http://x", "timeout": 0}, {"timeout": -1}):
            with self.assertRaises(ValueError, msg=bad):
                llm_config({"llm": bad})
        self.assertFalse(llm_config({})["configured"])
        self.assertFalse(llm_config({"llm": {"base_url": "http://x/v1"}})["configured"])
        m = llm_config({"llm": {"base_url": "https://user:pw@api.example.org/v1", "model": "m"}})
        self.assertEqual(m["host"], "api.example.org")

    def test_rate_limit(self):
        ANALYSE_TIMES.clear()
        self.addCleanup(ANALYSE_TIMES.clear)
        self.assertTrue(all(analyse_allowed(100.0 + i) for i in range(6)))
        self.assertFalse(analyse_allowed(110.0))
        self.assertTrue(analyse_allowed(161.0))


# --------------------------------------------------------------------------- PDF text


class ExtractorTests(unittest.TestCase):
    def test_pdftotext_command_and_temp_file_removed(self):
        calls = []

        def run(cmd, **kw):
            calls.append((cmd, kw, Path(cmd[-2]).read_bytes()))
            return subprocess.CompletedProcess(cmd, 0, GERMAN.encode(), b"")
        text, how = extract_pdf_text(b"%PDF-1.4 fake", which=lambda n: "/usr/bin/" + n, run=run)
        self.assertEqual((text, how), (GERMAN, "pdftotext"))
        cmd, kw, data = calls[0]
        self.assertEqual(cmd[:2], ["pdftotext", "-layout"])
        self.assertEqual(cmd[-1], "-")
        self.assertEqual(data, b"%PDF-1.4 fake")
        self.assertIn("timeout", kw)
        self.assertFalse(Path(cmd[-2]).exists())

    def test_failures(self):
        which = lambda n: "/usr/bin/" + n  # noqa: E731
        with self.assertRaises(ContractError) as ctx:
            extract_pdf_text(b"<html>", which=which)
        self.assertEqual(ctx.exception.status, 422)

        def empty(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, b"\f\n  \f", b"")
        with self.assertRaises(ContractError) as ctx:
            extract_pdf_text(b"%PDF-1.4", which=which, run=empty)
        self.assertIn("no selectable text", str(ctx.exception))

        def slow(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        with self.assertRaises(ContractError):
            extract_pdf_text(b"%PDF-1.4", which=which, run=slow)

        def broken(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 1, b"", b"Syntax Error: broken xref\n")
        with self.assertRaises(ContractError) as ctx:
            extract_pdf_text(b"%PDF-1.4", which=which, run=broken)
        self.assertIn("broken xref", str(ctx.exception))

        with mock.patch("importlib.util.find_spec", return_value=None), \
                self.assertRaises(ContractError) as ctx:
            extract_pdf_text(b"%PDF-1.4", which=lambda n: None)
        self.assertIn("poppler-utils", str(ctx.exception))

    @unittest.skipUnless(shutil.which("pdftotext"), "pdftotext is not installed")
    def test_real_extraction_of_the_fixture(self):
        text, how = extract_pdf_text(FIXTURE.read_bytes())
        out, counts = redact_contract(text)
        self.assertEqual(how, "pdftotext")
        for kept in ("32,50 ct/kWh", "12,90", "19 %", "01.03.2026"):
            self.assertIn(kept, out)
        for gone in ("kunde@example.com", "3704", "4471 2290 16"):
            self.assertNotIn(gone, out)
        self.assertEqual((counts["iban"], counts["email"], counts["number"]), (1, 1, 1))


# --------------------------------------------------------------------------- routes


class RouteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cfg_path = Path(tmp.name) / "config.toml"
        self.cfg_path.write_text("")
        self.cfg = load_config(self.cfg_path)
        self.cfg["server"]["require_token_always"] = True
        keys = [{"name": "a", "scope": "admin", "hash": hash_token("admin-token"), "created": None},
                {"name": "r", "scope": "read", "hash": hash_token("read-token"), "created": None}]
        H = type("H", (Handler,), {"cfg": self.cfg, "cfg_path": self.cfg_path, "monitor": None,
                                   "store": None, "keys": keys, "throttle": None,
                                   "trusted_proxies": []})
        server = serve(H)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(self, path, body=b"", ctype="application/json", token="admin-token", method="POST"):
        headers = {"X-Powermon-Token": token}
        if method == "POST":
            headers.update({"Content-Type": ctype, "X-Powermon-Write": "1"})
        req = urllib.request.Request(self.base + path, method=method, headers=headers,
                                     data=body if method == "POST" else None)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_guards(self):
        text = json.dumps({"text": GERMAN}).encode()
        self.assertEqual(self.call("/api/contract/text", b"%PDF-1.4", "application/json")[0], 403)
        self.assertEqual(self.call("/api/contract/analyse", text, "application/pdf")[0], 403)
        self.assertEqual(self.call("/api/settings", b"%PDF-1.4", "application/pdf")[0], 403)
        self.assertEqual(self.call("/api/contract/analyse", b"x" * (256 * 1024 + 1))[0], 413)
        self.assertEqual(self.call("/api/contract/text", b"%PDF-1.4", "application/pdf",
                                   token="read-token")[0], 403)
        self.assertEqual(self.call("/api/contract/analyse", text, token="read-token")[0], 403)
        status, out = self.call("/api/contract/analyse", text)
        self.assertEqual(status, 409)
        self.assertIn("[llm]", out["error"])
        self.cfg["server"]["settings_writable"] = False
        self.assertEqual(self.call("/api/contract/analyse", text)[0], 403)
        self.assertEqual(self.call("/api/contract/text", b"%PDF-1.4", "application/pdf")[0], 403)

    def test_oversize_pdf_is_refused_before_reading(self):
        # The cap is judged from Content-Length; the body never needs to arrive.
        self.assertEqual(write_refusal("POST", {
            "Content-Type": "application/pdf", "X-Powermon-Write": "1", "Host": "127.0.0.1",
            "Content-Length": str(10 * 1024 * 1024 + 1)}, True, (), "application/pdf",
            powermon.CONTRACT_PDF_MAX)[0], 413)

    def test_text_route_redacts(self):
        with mock.patch.object(powermon, "extract_pdf_text",
                               return_value=("IBAN DE89 3704 0044 0532 0130 00\n" + GERMAN, "pypdf")):
            status, out = self.call("/api/contract/text", b"%PDF-1.4", "application/pdf")
        self.assertEqual(status, 200, out)
        self.assertEqual(out["extractor"], "pypdf")
        self.assertEqual(out["redactions"]["iban"], 1)
        self.assertIn("32,50 ct/kWh", out["text"])
        self.assertFalse(out["truncated"])

    def test_settings_show_the_contract_status_to_admin_only(self):
        self.cfg["llm"].update(base_url="http://127.0.0.1:11434/v1", model="m", api_key=KEY)
        status, admin = self.call("/api/settings", token="admin-token", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual({k: admin["contract"][k] for k in ("llm", "host", "model", "local")},
                         {"llm": True, "host": "127.0.0.1", "model": "m", "local": True})
        self.assertIn(admin["contract"]["pdf"], ("pdftotext", "pypdf", None))
        self.assertNotIn(KEY, json.dumps(admin))
        status, read = self.call("/api/settings", token="read-token", method="GET")
        self.assertIsNone(read["contract"])
        self.assertFalse(settings_payload(load_config(self.cfg_path), self.cfg_path,
                                          True)["contract"]["llm"])


if __name__ == "__main__":
    unittest.main()
