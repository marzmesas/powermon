"""MQTT publishing: packet bytes, state and discovery documents, and a fake broker.

Run from the repository root:  python3 -m unittest discover -s tests
"""
import contextlib
import io
import json
import socket
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import powermon as pm  # noqa: E402

CFG = {"mqtt": {**pm.DEFAULTS["mqtt"], "host": "127.0.0.1"}}


def payload(**over):
    p = {"power": {"total_w": 123.5, "cpu_w": 40.0, "gpu_w": None, "cost_per_h": 0.02},
         "cpu": {"temp": 55.0}, "gpu": {"temp": None, "util": None},
         "busy": True, "alert": {"active": False, "since": None},
         "totals": {"all": {"kwh": 12.5, "cost": 3.1}, "today": {"cost": 0.4},
                    "month": {"cost": 1.2}},
         "meta": {"currency": "EUR", "version": "9.9"}}
    p.update(over)
    return p


class Packets(unittest.TestCase):
    def test_remaining_length(self):
        enc = pm.mqtt_remaining_length
        self.assertEqual(enc(0), b"\x00")
        self.assertEqual(enc(127), b"\x7f")
        self.assertEqual(enc(128), b"\x80\x01")
        self.assertEqual(enc(16383), b"\xff\x7f")
        self.assertEqual(enc(16384), b"\x80\x80\x01")

    def test_string_prefix_counts_bytes(self):
        self.assertEqual(pm.mqtt_str("°C"), b"\x00\x03\xc2\xb0C")

    def test_connect_plain(self):
        self.assertEqual(
            pm.mqtt_connect("id", 60),
            b"\x10\x0e\x00\x04MQTT\x04\x02\x00\x3c\x00\x02id")

    def test_connect_credentials_and_will(self):
        self.assertEqual(
            pm.mqtt_connect("id", 60, "u", "p"),
            b"\x10\x14\x00\x04MQTT\x04\xc2\x00\x3c\x00\x02id\x00\x01u\x00\x01p")
        self.assertEqual(
            pm.mqtt_connect("id", 60, will_topic="a/b", will_payload=b"offline"),
            b"\x10\x1c\x00\x04MQTT\x04\x26\x00\x3c\x00\x02id\x00\x03a/b\x00\x07offline")

    def test_password_without_username_is_not_sent(self):
        self.assertEqual(pm.mqtt_connect("id", 60, "", "secret"), pm.mqtt_connect("id", 60))

    def test_publish(self):
        self.assertEqual(pm.mqtt_publish("a/b", b"hi"), b"\x30\x07\x00\x03a/bhi")
        self.assertEqual(pm.mqtt_publish("a/b", b"hi", True), b"\x31\x07\x00\x03a/bhi")

    def test_long_publish_uses_two_length_bytes(self):
        pkt = pm.mqtt_publish("t", b"x" * 200, True)
        self.assertEqual(pkt[:3], b"\x31\xcb\x01")   # 2 + 1 + 200 = 203


class Documents(unittest.TestCase):
    def test_state_omits_none_and_maps_booleans(self):
        state = pm.mqtt_state(payload())
        self.assertNotIn("gpu_w", state)
        self.assertNotIn("gpu_temp", state)
        self.assertNotIn("gpu_util", state)
        self.assertEqual(state["busy"], "ON")
        self.assertEqual(state["alert"], "OFF")
        self.assertEqual(state["power_w"], 123.5)
        self.assertEqual(state["energy_kwh"], 12.5)
        self.assertEqual(state["month_cost"], 1.2)

    def test_zero_is_kept(self):
        p = payload()
        p["power"]["gpu_w"] = 0.0
        self.assertEqual(pm.mqtt_state(p)["gpu_w"], 0.0)

    def test_discovery(self):
        docs = pm.mqtt_discovery(pm.mqtt_config(CFG), "box", "EUR", "9.9")
        by_id = {d["unique_id"]: (t, d) for t, d in docs}
        self.assertEqual(len(by_id), len(docs))
        self.assertIn("homeassistant/sensor/box/energy/config", by_id["powermon_box_energy"][0])
        energy = by_id["powermon_box_energy"][1]
        self.assertEqual((energy["state_class"], energy["unit_of_measurement"],
                          energy["device_class"]), ("total_increasing", "kWh", "energy"))
        self.assertEqual(energy["value_template"], "{{ value_json.energy_kwh }}")
        self.assertEqual(energy["state_topic"], "powermon/box/state")
        self.assertEqual(energy["device"]["identifiers"], ["powermon_box"])
        money = [d for _, d in docs if d.get("device_class") == "monetary"]
        self.assertTrue(money)
        for d in money:
            self.assertNotEqual(d.get("state_class"), "measurement")
            self.assertEqual(d["unit_of_measurement"], "EUR")
        self.assertEqual(by_id["powermon_box_cost_total"][1]["state_class"], "total")
        self.assertTrue(by_id["powermon_box_busy"][0].startswith("homeassistant/binary_sensor/"))

    def test_config_validation(self):
        for bad in ({"port": 0}, {"port": 70000}, {"port": "x"}, {"interval_s": 0},
                    {"topic": "a/#"}):
            with self.assertRaises(ValueError, msg=bad):
                pm.mqtt_config({"mqtt": bad})
        self.assertEqual(pm.mqtt_config({})["host"], "")

    def test_node_is_sanitised(self):
        self.assertEqual(pm.mqtt_node("my box.lan"), "my_box_lan")


def read_packet(conn):
    def need(n):
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise EOFError
            buf += chunk
        return buf
    first = need(1)[0]
    length, shift = 0, 0
    while True:
        b = need(1)[0]
        length |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            break
    return first, need(length)


class FakeBroker:
    def __init__(self, connack_code=0, stop_after=None):
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.connack_code, self.stop_after = connack_code, stop_after
        self.connect_packet, self.published = None, []
        self.done = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        conn, _ = self.server.accept()
        conn.settimeout(5)
        try:
            first, body = read_packet(conn)
            self.connect_packet = (first, body)
            conn.sendall(bytes([0x20, 0x02, 0x00, self.connack_code]))
            while True:
                first, body = read_packet(conn)
                if first >> 4 == 3:
                    n = int.from_bytes(body[:2], "big")
                    self.published.append((first & 1, body[2:2 + n].decode(), body[2 + n:]))
                    if self.stop_after and any(t.endswith("/state") for _, t, _ in self.published):
                        self.done.set()
                elif first >> 4 == 14:
                    break
        except (EOFError, OSError):
            pass
        finally:
            self.done.set()
            conn.close()
            self.server.close()


class Integration(unittest.TestCase):
    def publisher(self, broker, **kw):
        m = pm.mqtt_config({"mqtt": {"host": "127.0.0.1", "port": broker.port,
                                     "username": "u", "password": "hunter2"}})
        return pm.MqttPublisher(m, payload, **kw)

    def test_publishes_availability_discovery_and_state(self):
        broker = FakeBroker(stop_after=True)
        pub = self.publisher(broker)
        pub.start()
        self.assertTrue(broker.done.wait(2))
        self.assertTrue(pub.connected)
        pub.stop()
        broker.thread.join(2)
        node = pm.mqtt_node()
        retained = {t: r for r, t, _ in broker.published}
        self.assertEqual(retained[f"powermon/{node}/availability"], 1)
        online = [b for _, t, b in broker.published if t.endswith("/availability")]
        self.assertEqual(online, [b"online"])
        discovery = [t for t in retained if t.startswith("homeassistant/")]
        self.assertEqual(len(discovery), 13)
        self.assertTrue(all(retained[t] == 1 for t in discovery))
        self.assertEqual(retained[f"powermon/{node}/state"], 0)
        state = json.loads(next(b for _, t, b in broker.published if t.endswith("/state")))
        self.assertEqual(state["power_w"], 123.5)
        # The will is registered, retained, on the availability topic.
        body = broker.connect_packet[1]
        self.assertIn(f"powermon/{node}/availability".encode() + b"\x00\x07offline", body)
        self.assertNotIn(b"hunter2", json.dumps(broker.published, default=str).encode())

    def test_refused_connack_reports_failure(self):
        broker = FakeBroker(connack_code=5)
        delays = []
        pub = self.publisher(broker, wait=lambda s: delays.append(s) or True)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            pub.run()           # returns after the first failure: wait says "stop"
        self.assertFalse(pub.connected)
        self.assertIn("CONNACK code 5", pub.last_error)
        self.assertEqual(delays, [5.0])
        self.assertEqual(err.getvalue().count("\n"), 1)
        self.assertNotIn("hunter2", err.getvalue())

    def test_backoff_doubles_to_a_cap(self):
        calls = []

        def refuse(host, port, tls):
            raise ConnectionRefusedError("down")

        def wait(s):
            calls.append(s)
            return len(calls) >= 9
        pub = pm.MqttPublisher(pm.mqtt_config(CFG), payload, connect=refuse, wait=wait)
        with contextlib.redirect_stderr(io.StringIO()):
            pub.run()
        self.assertEqual(calls, [5, 10, 20, 40, 80, 160, 300, 300, 300])


class Health(unittest.TestCase):
    def test_disconnected_warns_only_when_configured(self):
        from test_health import FakeMonitor, totals
        mon = FakeMonitor()
        base = {"sampling": {"interval": 2.0}}
        self.assertNotIn("mqtt_disconnected", {i["code"] for i in pm.health(mon, totals(), base)["issues"]})
        mon.mqtt = type("P", (), {"connected": False})()
        issues = pm.health(mon, totals(), base)["issues"]
        self.assertEqual([i["level"] for i in issues if i["code"] == "mqtt_disconnected"], ["warn"])
        mon.mqtt.connected = True
        self.assertNotIn("mqtt_disconnected", {i["code"] for i in pm.health(mon, totals(), base)["issues"]})


if __name__ == "__main__":
    unittest.main()
