# Copyright 2026 Iván Moreno Mendoza
# SPDX-License-Identifier: Apache-2.0

"""The static C client (client/c) against the simulator's save service and
against save files as a console leaves them: every status it can return."""

import json
import socket
import threading
import unittest

from support import Scratch, compile_munc, munc

from mdk import sim


class Client(Scratch):
    @classmethod
    def setUpClass(cls):
        import shutil
        import tempfile
        from pathlib import Path
        cls.build = Path(tempfile.mkdtemp(prefix="munc-", dir="/tmp"))
        cls.addClassCleanup(shutil.rmtree, cls.build, True)
        cls.program = compile_munc(cls.build)

    def run_munc(self, arguments, stdin=b"", **facts):
        return munc(self.program, arguments, {"MUN_" + name.upper(): str(value) for name, value in facts.items()}, stdin)

    # ---------------------------------------------------------------- facts
    def test_facts_inside_and_outside_a_session(self):
        outside = self.run_munc(["facts"])
        self.assertEqual(outside["in_session"], "0")
        self.assertEqual(outside["card_id"], "(absent)")
        inside = self.run_munc(["facts"], session="s1", card_id="mun.test", content_version="1.2.0",
                               runtime_profile="linux-arm64-gl-v0", display_mode="1920x1080", content_dir="/c",
                               save_file="/w/save.json", save_socket="/w/save.sock")
        self.assertEqual(inside["in_session"], "1")
        self.assertEqual((inside["card_id"], inside["content_version"], inside["display_mode"]),
                         ("mun.test", "1.2.0", "1920x1080"))
        self.assertEqual(inside["out_of_range"], "(absent)")
        # An empty variable is an absent fact.
        self.assertEqual(self.run_munc(["facts"], session="")["in_session"], "0")

    def test_content_paths_stay_inside_the_content(self):
        self.assertEqual(self.run_munc(["path", "a.txt"])["status"], "unavailable")
        good = self.run_munc(["path", "sounds/blip.wav"], content_dir="/run/mun/card/content")
        self.assertEqual((good["status"], good["path"]), ("ok", "/run/mun/card/content/sounds/blip.wav"))
        for bad in ("/etc/passwd", "../mun.toml", "a/../b", "a//b", "./a", "a/.", "a/", ""):
            self.assertEqual(self.run_munc(["path", bad], content_dir="/c")["status"], "argument", bad)
        self.assertEqual(self.run_munc(["path", "x" * 80], content_dir="/c")["status"], "too_large")

    # ---------------------------------------------------------------- write
    def test_a_save_is_confirmed(self):
        card = self.card()
        gate = self.gate(card)
        got = self.run_munc(["write", "3", "5000"], b'{"count": 7, "name": "caf\xc3\xa9"}', save_socket=gate.path)
        self.assertEqual((got["status"], got["code"], got["message"]), ("ok", "", ""))
        written = (card.directory / "save.json").read_bytes()
        self.assertEqual(int(got["bytes"]), len(written))
        document = json.loads(written)
        self.assertEqual((document["schema"], document["payload"]), (3, {"count": 7, "name": "café"}))

    def test_line_breaks_in_the_payload_do_not_break_the_request(self):
        card = self.card()
        gate = self.gate(card)
        got = self.run_munc(["write", "1", "5000"], b'{\n  "a": 1,\r\n  "b": [1,\n 2]\n}\n', save_socket=gate.path)
        self.assertEqual(got["status"], "ok")
        self.assertEqual(json.loads((card.directory / "save.json").read_bytes())["payload"], {"a": 1, "b": [1, 2]})

    def test_refusals_carry_the_console_s_code_and_sentence(self):
        for code, sentence in sim.ANSWERED_FAULTS.items():
            with self.subTest(code=code):
                card = sim.SimCard(self.scratch / f"card-{code}", "mun.test", "1.0.0", self.card().rules)
                gate = sim.SaveGate(self.scratch / f"{code}.sock", card, "stest", fail={code: -1})
                self.addCleanup(gate.close)
                got = self.run_munc(["write", "1", "5000"], b'{"n":1}', save_socket=gate.path)
                self.assertEqual((got["status"], got["code"], got["message"], got["bytes"]),
                                 ("refused", code, sentence, "0"))

    def test_a_save_the_console_could_not_attend_is_refused_and_the_next_one_works(self):
        card = self.card()
        gate = self.gate(card, fail={"save_failed": 1})
        first = self.run_munc(["write", "1", "5000"], b'{"n":1}', save_socket=gate.path)
        self.assertEqual((first["status"], first["code"], first["message"], first["bytes"]),
                         ("refused", "save_failed", sim.ANSWERED_FAULTS["save_failed"], "0"))
        self.assertFalse((card.directory / "save.json").exists())
        second = self.run_munc(["write", "1", "5000"], b'{"n":2}', save_socket=gate.path)
        self.assertEqual(second["status"], "ok")
        self.assertEqual(json.loads((card.directory / "save.json").read_bytes())["payload"], {"n": 2})

    def test_a_connection_closed_without_an_answer_is_an_error_not_a_wait(self):
        # The client was given ten minutes; the call ends as soon as the
        # console closes, with an error. (munc itself is killed after 20 s.)
        gate = self.gate(fail={"closed": 1})
        got = self.run_munc(["write", "1", "600000"], b'{"n":1}', save_socket=gate.path)
        self.assertEqual((got["status"], got["code"], got["bytes"]), ("io", "", "0"))
        self.assertEqual(self.run_munc(["write", "1", "5000"], b'{"n":2}', save_socket=gate.path)["status"], "ok")

    def test_the_console_can_refuse_what_the_client_let_through(self):
        # Valid JSON, and text no card can hold: half of a surrogate pair.
        gate = self.gate()
        got = self.run_munc(["write", "1", "5000"], b'{"t":"\\ud800"}', save_socket=gate.path)
        self.assertEqual((got["status"], got["code"]), ("refused", "save_invalid"))

    def test_text_outside_ascii_counts_as_its_utf8_bytes(self):
        card = self.card()
        gate = self.gate(card)
        payload = json.dumps({"t": "ñ" * 30000}, ensure_ascii=False).encode("utf-8")
        self.assertEqual(self.run_munc(["write", "1", "5000"], payload, save_socket=gate.path)["status"], "ok")
        self.assertEqual(json.loads((card.directory / "save.json").read_bytes())["payload"], {"t": "ñ" * 30000})

    def test_what_the_client_refuses_before_asking(self):
        gate = self.gate()
        socket_path = gate.path
        self.assertEqual(self.run_munc(["write", "1", "500"], b'{"n":1}')["status"], "unavailable")
        self.assertEqual(self.run_munc(["write", "0", "500"], b'{"n":1}', save_socket=socket_path)["status"], "argument")
        for payload in (b"[1]", b'"text"', b"{", b'{"a":1} x', b'{"a":01}', b'{"a":"\x01"}', b'{"a":1}{"b":2}', b"",
                        b'{"a":"\\x"}', b'{"a":tru}', b"{'a':1}"):
            self.assertEqual(self.run_munc(["write", "1", "500"], payload, save_socket=socket_path)["status"],
                             "argument", payload)
        big = b'{"b":"' + b"x" * 65536 + b'"}'
        self.assertEqual(self.run_munc(["write", "1", "500"], big, save_socket=socket_path)["status"], "too_large")
        self.assertEqual(gate.saves, [])
        deep = b'{"a":' + b"[" * 70 + b"]" * 70 + b"}"                    # deeper than a console reads
        self.assertEqual(self.run_munc(["write", "1", "500"], deep, save_socket=socket_path)["status"], "argument")

    def test_no_answer_in_time_is_a_timeout(self):
        card = self.card()
        gate = self.gate(card, save_delay=1.0)
        got = self.run_munc(["write", "1", "200"], b'{"n":1}', save_socket=gate.path)
        self.assertEqual(got["status"], "timeout")

    def test_one_save_at_a_time(self):
        gate = self.gate(save_delay=0.5)
        results = {}
        first = threading.Thread(target=lambda: results.update(
            first=self.run_munc(["write", "1", "5000"], b'{"n":1}', save_socket=gate.path)))
        first.start()
        import time
        time.sleep(0.15)
        second = self.run_munc(["write", "1", "5000"], b'{"n":2}', save_socket=gate.path)
        first.join()
        self.assertEqual((second["status"], second["code"]), ("refused", "save_busy"))
        self.assertEqual(results["first"]["status"], "ok")

    def test_socket_failures(self):
        self.assertEqual(self.run_munc(["write", "1", "500"], b'{"n":1}',
                                       save_socket=self.scratch / "nothing.sock")["status"], "io")
        self.assertEqual(self.run_munc(["write", "1", "500"], b'{"n":1}', save_socket="/tmp/" + "x" * 200)["status"], "io")

    def fake_console(self, reply: bytes):
        """A listener that reads a request and answers with `reply` (or just closes)."""
        path = self.scratch / "fake.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        listener.listen(1)
        self.addCleanup(listener.close)

        def serve():
            connection, _ = listener.accept()
            with connection:
                connection.recv(65536)
                if reply:
                    connection.sendall(reply)
        threading.Thread(target=serve, daemon=True).start()
        return path

    def test_a_console_that_closes_without_answering(self):
        got = self.run_munc(["write", "1", "2000"], b'{"n":1}', save_socket=self.fake_console(b""))
        self.assertEqual(got["status"], "io")

    def test_answers_the_client_cannot_read(self):
        for reply in (b"hello\n", b'{"type":"saved"}\n', b'{"type":"other","ok":true}\n', b'{"type":"saved","ok":1}\n',
                      b'{"type":"saved","ok":false}\n', b'{"type":"saved","ok":false,"error":{"message":"x"}}\n',
                      b'{"type":"saved","ok":true,"bytes":-1}\n', b"x" * 5000 + b"\n"):
            got = self.run_munc(["write", "1", "2000"], b'{"n":1}', save_socket=self.fake_console(reply))
            self.assertEqual((got["status"], got["code"], got["bytes"]), ("protocol", "", "0"), reply)
            (self.scratch / "fake.sock").unlink()

    def test_escapes_in_an_answer_are_decoded(self):
        reply = (b'{"type":"saved","ok":false,"error":{"code":"no_space",'
                 b'"message":"l\\u00ednea \\"1\\"\\n\\ud83c\\udfae \\ud800 fin","detail":"x"}}\n')
        got = munc(self.program, ["write", "1", "2000"], {"MUN_SAVE_SOCKET": str(self.fake_console(reply))}, b'{"n":1}')
        self.assertEqual(got["status"], "refused")
        self.assertEqual(got["message"], 'línea "1"')           # munc prints lines: the rest is on the next one
        long_reply = ('{"type":"saved","ok":false,"error":{"code":"' + "c" * 60 + '","message":"' + "é" * 400
                      + '"}}\n').encode("utf-8")
        (self.scratch / "fake.sock").unlink()
        got = self.run_munc(["write", "1", "2000"], b'{"n":1}', save_socket=self.fake_console(long_reply))
        self.assertEqual(got["code"], "c" * 31)
        self.assertEqual(got["message"], "é" * 95)               # cut at a character boundary, within 191 bytes

    # ---------------------------------------------------------------- load
    def save_file(self, content: bytes):
        path = self.scratch / "save.json"
        path.write_bytes(content)
        return path

    def envelope(self, **changes) -> bytes:
        document = {"format": "mun-save/1", "game": "mun.test", "content_version": "1.0.0", "schema": 2,
                    "saved_at": "2026-11-10T10:00:00Z", "session": "s1", "insertion": "i1",
                    "payload": {"count": 7, "nested": {"a": [1, 2, {"b": None}]}, "text": "a \"q\" \\ ñ"}}
        document.update(changes)
        return (json.dumps({k: v for k, v in document.items() if v is not ...}, ensure_ascii=False) + "\n").encode("utf-8")

    def test_no_save(self):
        self.assertEqual(self.run_munc(["load"])["status"], "unavailable")
        got = self.run_munc(["load"], save_file=self.scratch / "missing.json")
        self.assertEqual((got["status"], got["untouched"]), ("no_save", "1"))

    def test_a_save_is_loaded_with_its_payload_as_the_game_sent_it(self):
        got = self.run_munc(["load"], save_file=self.save_file(self.envelope()))
        self.assertEqual((got["status"], got["schema"], got["recovered"]), ("ok", "2", "0"))
        self.assertEqual(json.loads(got["payload"]), {"count": 7, "nested": {"a": [1, 2, {"b": None}]},
                                                      "text": "a \"q\" \\ ñ"})
        self.assertEqual(int(got["length"]), len(got["payload"].encode("utf-8")))

    def test_a_save_written_by_the_simulated_console_is_loaded(self):
        card = self.card()
        gate = self.gate(card)
        self.assertEqual(self.run_munc(["write", "4", "5000"], b'{"count":12}', save_socket=gate.path)["status"], "ok")
        target = self.scratch / "restored.json"
        card.restore(target)
        got = self.run_munc(["load"], save_file=target)
        self.assertEqual((got["status"], got["schema"], got["payload"]), ("ok", "4", '{"count": 12}'))

    def test_a_recovered_save_says_so(self):
        got = self.run_munc(["load"], save_file=self.save_file(self.envelope(recovered_from="save.json.prev")))
        self.assertEqual((got["status"], got["recovered"]), ("ok", "1"))

    def test_files_that_are_not_a_save_of_this_generation_are_damaged(self):
        cases = [b"", b"{ torn", b"[1,2]", self.envelope()[:-20], self.envelope(format="neptune-save/1"),
                 self.envelope(format=...), self.envelope(schema=0), self.envelope(schema="2"), self.envelope(schema=...),
                 self.envelope(schema=2 ** 40), self.envelope(payload=[1]), self.envelope(payload=...),
                 self.envelope() + b"trailing", b"x" * (112 * 1024 + 1),
                 self.envelope(payload={"a": json.loads("[" * 70 + "]" * 70)}),
                 b'{"format":"mun-save/1","schema":1,"payload":{},"payload":{}}']
        for content in cases:
            got = self.run_munc(["load"], save_file=self.save_file(content))
            self.assertEqual((got["status"], got["untouched"]), ("damaged", "1"), content[:60])

    def test_a_buffer_too_small_for_the_payload(self):
        path = self.save_file(self.envelope(payload={"t": "x" * 100}))
        got = self.run_munc(["load", "50"], save_file=path)
        self.assertEqual((got["status"], got["untouched"]), ("too_large", "1"))
        self.assertEqual(self.run_munc(["load", "200"], save_file=path)["status"], "ok")

    def test_a_restored_payload_can_be_longer_than_the_one_saved(self):
        # The console keeps the value and writes it with its own spacing: the
        # largest payload a game may send comes back a byte longer, and one
        # with many members longer still. MUN_SAVE_LOAD_MAX_BYTES holds any.
        payload = {"blob": "x" * (65536 - 11)}
        got = self.run_munc(["load"], save_file=self.save_file(self.envelope(payload=payload)))
        self.assertEqual((got["status"], int(got["length"])), ("ok", 65536 + 1))
        self.assertEqual(self.run_munc(["load", "65537"], save_file=self.scratch / "save.json")["status"], "too_large")
        many = {"v": [0] * 32000}
        self.assertLessEqual(len(json.dumps(many, separators=(",", ":"))), 65536)
        got = self.run_munc(["load"], save_file=self.save_file(self.envelope(payload=many)))
        self.assertEqual(got["status"], "ok")
        self.assertGreater(int(got["length"]), 95000)            # half as long again on the card
        self.assertEqual(json.loads(got["payload"]), many)
        nested = {"a": json.loads("[" * 60 + "]" * 60)}          # as deep as a console reads
        got = self.run_munc(["load"], save_file=self.save_file(self.envelope(payload=nested)))
        self.assertEqual((got["status"], json.loads(got["payload"])), ("ok", nested))

    # ---------------------------------------------------------------- helper
    def test_json_int(self):
        self.assertEqual(self.run_munc(["int", "count"], b'{"a":"count","count": 42 }'),
                         {"status": "ok", "value": "42"})
        self.assertEqual(self.run_munc(["int", "n"], b'{"n":-9223372036854775808}')["value"], "-9223372036854775808")
        self.assertEqual(self.run_munc(["int", "n"], b'{"n":9223372036854775807}')["value"], "9223372036854775807")
        self.assertEqual(self.run_munc(["int", "missing"], b'{"n":1}'), {"status": "absent", "value": "-7777"})
        for bad in (b'{"n":9223372036854775808}', b'{"n":1.0}', b'{"n":1e3}', b'{"n":"1"}', b'{"n":true}', b'{"n":1,"n":2}',
                    b'[1]', b'{"n":1', b""):
            self.assertEqual(self.run_munc(["int", "n"], bad), {"status": "damaged", "value": "-7777"}, bad)


if __name__ == "__main__":
    unittest.main()
