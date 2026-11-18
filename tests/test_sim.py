"""The simulator's save service against the console's contract (MUN OS
docs/saves.md): what a request must be, what each refusal says, what the
card holds afterwards, and what a session is handed back."""

import json
import threading
import time
import unittest

from support import SAVES, Scratch, ask, save_line

from mdk import sim


class SaveRequests(Scratch):
    def test_a_save_is_confirmed_once_the_card_holds_it(self):
        card = self.card()
        gate = self.gate(card)
        answer = ask(gate.path, save_line({"level": 3, "name": "ñandú"}, schema=2))
        self.assertEqual(answer["type"], "saved")
        self.assertTrue(answer["ok"])
        written = (card.directory / "save.json").read_bytes()
        self.assertEqual(answer["bytes"], len(written))
        document = json.loads(written)
        self.assertEqual(document["format"], "mun-save/1")
        self.assertEqual(document["game"], "mun.test")
        self.assertEqual(document["content_version"], "1.0.0")
        self.assertEqual(document["schema"], 2)
        self.assertEqual(document["payload"], {"level": 3, "name": "ñandú"})
        # The console's own rule for an envelope of this card accepts it.
        self.assertIsNone(SAVES.envelope_problem(document, card.info, "mun-save/1"))

    def test_the_previous_save_is_kept_beside_the_new_one(self):
        card = self.card()
        gate = self.gate(card)
        self.assertTrue(ask(gate.path, save_line({"n": 1}))["ok"])
        first = (card.directory / "save.json").read_bytes()
        self.assertTrue(ask(gate.path, save_line({"n": 2}))["ok"])
        self.assertEqual((card.directory / "save.json.prev").read_bytes(), first)
        self.assertEqual(json.loads((card.directory / "save.json").read_bytes())["payload"], {"n": 2})
        self.assertFalse((card.directory / "save.json.tmp").exists())

    def test_requests_the_console_refuses_as_invalid(self):
        gate = self.gate()
        for line in (save_line({"n": 1}, schema=0), save_line({"n": 1}, schema="1"), save_line({"n": 1}, schema=True),
                     save_line({"n": 1}, schema=1.5), save_line([1, 2]), save_line("text"), save_line(None),
                     save_line({"n": 1}, kind="store"), b'{"schema":1,"data":{}}\n', b'{"type":"save","schema":1}\n'):
            answer = ask(gate.path, line)
            self.assertEqual((answer["ok"], answer["error"]["code"]), (False, "save_invalid"), line)
            self.assertTrue(answer["error"]["message"])
        self.assertFalse((self.scratch / "card" / "saves").exists())

    def test_a_payload_over_64_kib_is_refused(self):
        gate = self.gate()
        answer = ask(gate.path, save_line({"blob": "x" * 65536}))
        self.assertEqual(answer["error"]["code"], "save_too_large")
        # Just under: {"blob":"…"} is 11 bytes of framing.
        self.assertTrue(ask(gate.path, save_line({"blob": "x" * (65536 - 11)}))["ok"])

    def test_the_bound_counts_utf8_however_the_line_spells_it(self):
        # 30,000 two-byte characters are 60 KiB as UTF-8: inside the bound,
        # whether the line carries them as themselves or as \\uXXXX escapes
        # (then 180 KiB of line). 33,000 are 66 KiB: over it either way.
        card = self.card()
        gate = self.gate(card)
        for escaped in (False, True):
            line = (json.dumps({"type": "save", "schema": 1, "data": {"t": "ñ" * 30000}}, ensure_ascii=escaped)
                    + "\n").encode("utf-8")
            self.assertTrue(ask(gate.path, line)["ok"], f"escaped={escaped}")
            line = (json.dumps({"type": "save", "schema": 1, "data": {"t": "ñ" * 33000}}, ensure_ascii=escaped)
                    + "\n").encode("utf-8")
            self.assertEqual(ask(gate.path, line)["error"]["code"], "save_too_large", f"escaped={escaped}")
        self.assertEqual(json.loads((card.directory / "save.json").read_bytes())["payload"], {"t": "ñ" * 30000})

    def test_text_that_utf8_cannot_carry_is_no_save(self):
        gate = self.gate()
        answer = ask(gate.path, b'{"type":"save","schema":1,"data":{"t":"\\ud800"}}\n')
        self.assertEqual((answer["error"]["code"], answer["error"]["message"]),
                         ("save_invalid", "La partida contiene texto que no se puede guardar"))

    def test_a_save_the_console_confirms_is_one_it_reads_back_whole(self):
        # Many small values: compact they fit the payload's bound; spaced out
        # on the card they are half as long again, inside the file's bound.
        card = self.card()
        gate = self.gate(card)
        data = {"v": [0] * 32000}
        self.assertLessEqual(len(json.dumps(data, separators=(",", ":"))), sim.SAVE_MAX_BYTES)
        answer = ask(gate.path, save_line(data))
        _, limit = SAVES.save_bounds(card.info)
        if answer["ok"]:
            self.assertLessEqual(answer["bytes"], limit)
            restored = self.scratch / "restored.json"
            self.assertTrue(card.restore(restored)["copied"])
            self.assertEqual(json.loads(restored.read_bytes())["payload"], data)
        else:   # an earlier console's file bound was tighter than its payload bound
            self.assertEqual(answer["error"]["code"], "save_too_large")
            self.assertFalse((card.directory / "save.json").exists())

    def test_one_save_at_a_time(self):
        card = self.card()
        gate = self.gate(card, save_delay=0.4)
        answers = {}
        first = threading.Thread(target=lambda: answers.update(first=ask(gate.path, save_line({"n": 1}))))
        first.start()
        time.sleep(0.1)
        second = ask(gate.path, save_line({"n": 2}))
        first.join()
        self.assertEqual(second["error"]["code"], "save_busy")
        self.assertTrue(answers["first"]["ok"])
        self.assertEqual(json.loads((card.directory / "save.json").read_bytes())["payload"], {"n": 1})
        self.assertTrue(ask(gate.path, save_line({"n": 3}))["ok"])          # free again afterwards

    def test_a_line_that_is_not_a_json_object_gets_no_answer(self):
        gate = self.gate()
        for line in (b"not json\n", b"[1,2,3]\n", b'"save"\n', b"\n"):
            self.assertIsNone(ask(gate.path, line, timeout=0.4), line)

    def test_a_line_over_the_limit_is_answered_and_the_connection_closed(self):
        gate = self.gate()
        for line in (b"x" * (sim.SAVE_LINE_MAX + 70000),                    # never finished
                     b'{"type":"save","schema":1,"data":{"t":"' + b"x" * sim.SAVE_LINE_MAX + b'"}}\n'):
            answer = ask(gate.path, line, timeout=3.0)
            self.assertEqual((answer["ok"], answer["error"]["code"]), (False, "save_too_large"))
        self.assertTrue(ask(gate.path, save_line({"n": 1}))["ok"])          # the service goes on

    def test_a_line_nested_too_deep_gets_no_answer(self):
        gate = self.gate()
        deep = b'{"type":"save","schema":1,"data":{"a":' + b"[" * 64 + b"]" * 64 + b"}}\n"
        self.assertIsNone(ask(gate.path, deep, timeout=0.4))
        fits = b'{"type":"save","schema":1,"data":{"a":' + b"[" * 62 + b"]" * 62 + b"}}\n"
        self.assertTrue(ask(gate.path, fits)["ok"])

    def test_a_connection_that_asks_nothing_is_closed(self):
        gate = self.gate(request_timeout=0.3)
        self.assertEqual(ask(gate.path, b"", timeout=2.0), "")
        self.assertEqual(ask(gate.path, b'{"type":"save"', timeout=2.0), "")
        self.assertTrue(ask(gate.path, save_line({"n": 1}))["ok"])

    def test_one_request_per_connection(self):
        card = self.card()
        gate = self.gate(card)
        answer = ask(gate.path, save_line({"n": 1}) + save_line({"n": 2}))
        self.assertTrue(answer["ok"])
        time.sleep(0.2)
        self.assertEqual(json.loads((card.directory / "save.json").read_bytes())["payload"], {"n": 1})
        self.assertEqual(len(gate.saves), 1)

    def test_no_save_once_the_session_has_ended(self):
        card = self.card()
        gate = self.gate(card)
        gate.end_session()
        self.assertEqual(ask(gate.path, save_line({"n": 1}))["error"]["code"], "no_session")
        self.assertFalse(card.directory.exists())

    def test_an_answer_that_arrives_after_the_session_is_not_delivered(self):
        card = self.card()
        gate = self.gate(card, save_delay=0.3)
        answers = {}
        asking = threading.Thread(target=lambda: answers.update(got=ask(gate.path, save_line({"n": 1}), timeout=1.0)))
        asking.start()
        time.sleep(0.1)
        gate.end_session()
        asking.join()
        self.assertIsNone(answers["got"])
        # The write itself was already under way and completes on the card.
        self.assertEqual(json.loads((card.directory / "save.json").read_bytes())["payload"], {"n": 1})


class Faults(Scratch):
    def test_every_fault_is_answered_with_its_code_and_the_console_s_sentence(self):
        for code, sentence in sim.FAULTS.items():
            with self.subTest(code=code):
                card = sim.SimCard(self.scratch / f"card-{code}", "mun.test", "1.0.0", SAVES)
                gate = sim.SaveGate(self.scratch / f"{code}.sock", card, "stest", fail={code: -1})
                self.addCleanup(gate.close)
                answer = ask(gate.path, save_line({"n": 1}))
                self.assertEqual((answer["ok"], answer["error"]["code"], answer["error"]["message"]),
                                 (False, code, sentence))
                self.assertFalse((card.directory / "save.json").exists())

    def test_a_failed_save_leaves_the_previous_one_intact(self):
        card = self.card()
        gate = self.gate(card)
        self.assertTrue(ask(gate.path, save_line({"n": 1}))["ok"])
        before = (card.directory / "save.json").read_bytes()
        gate.fail["no_space"] = 1
        self.assertEqual(ask(gate.path, save_line({"n": 2}))["error"]["code"], "no_space")
        self.assertEqual((card.directory / "save.json").read_bytes(), before)
        self.assertTrue(ask(gate.path, save_line({"n": 3}))["ok"])          # ":1" failed one save only

    def test_parse_fail(self):
        self.assertEqual(sim.parse_fail(["no_space", "card_removed:2"]), {"no_space": -1, "card_removed": 2})
        self.assertEqual(sim.parse_fail(None), {})
        for bad in (["full"], ["no_space:x"]):
            with self.assertRaises(sim.MdkError):
                sim.parse_fail(bad)


class Restore(Scratch):
    def envelope(self, payload, schema=1, format="mun-save/1", game="mun.test"):
        return (json.dumps({"format": format, "game": game, "content_version": "1.0.0", "schema": schema,
                            "payload": payload}) + "\n").encode("utf-8")

    def test_no_save_on_the_card(self):
        self.assertEqual(self.card().restore(self.scratch / "save.json"), {"present": False})
        self.assertFalse((self.scratch / "save.json").exists())

    def test_the_current_save_is_copied(self):
        card = self.card()
        card.directory.mkdir(parents=True)
        (card.directory / "save.json").write_bytes(self.envelope({"n": 5}))
        outcome = card.restore(self.scratch / "save.json")
        self.assertEqual((outcome["copied"], outcome["recovered"]), (True, False))
        self.assertEqual(json.loads((self.scratch / "save.json").read_bytes())["payload"], {"n": 5})

    def test_a_missing_save_falls_back_to_the_previous_one_and_says_so(self):
        card = self.card()
        card.directory.mkdir(parents=True)
        (card.directory / "save.json.prev").write_bytes(self.envelope({"n": 4}))
        outcome = card.restore(self.scratch / "save.json")
        self.assertTrue(outcome["recovered"])
        document = json.loads((self.scratch / "save.json").read_bytes())
        self.assertEqual((document["payload"], document["recovered_from"]), ({"n": 4}, "save.json.prev"))

    def test_a_save_of_the_other_naming_generation_is_not_handed_out(self):
        card = self.card()
        card.directory.mkdir(parents=True)
        (card.directory / "save.json").write_bytes(self.envelope({"n": 1}, format="neptune-save/1"))
        outcome = card.restore(self.scratch / "save.json")
        self.assertEqual((outcome["copied"], outcome["error"]["code"]), (False, "save_other_generation"))
        self.assertFalse((self.scratch / "save.json").exists())

    def test_a_save_that_does_not_parse_is_still_handed_to_the_game(self):
        card = self.card()
        card.directory.mkdir(parents=True)
        (card.directory / "save.json").write_bytes(b"{ torn")
        outcome = card.restore(self.scratch / "save.json")
        self.assertTrue(outcome["copied"])
        self.assertEqual((self.scratch / "save.json").read_bytes(), b"{ torn")

    def test_saving_over_a_damaged_save_sets_it_aside_and_never_makes_it_the_previous_copy(self):
        card = self.card()
        card.directory.mkdir(parents=True)
        (card.directory / "save.json").write_bytes(b"{ torn")
        gate = self.gate(card)
        self.assertTrue(ask(gate.path, save_line({"n": 9}))["ok"])
        aside = [path.name for path in card.directory.iterdir() if path.name.startswith("save.json.damaged-")]
        self.assertEqual(len(aside), 1)
        self.assertEqual((card.directory / aside[0]).read_bytes(), b"{ torn")
        self.assertFalse((card.directory / "save.json.prev").exists())
        self.assertEqual(json.loads((card.directory / "save.json").read_bytes())["payload"], {"n": 9})


if __name__ == "__main__":
    unittest.main()
