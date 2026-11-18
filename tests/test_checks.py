
"""mdk's checks of a build: what the ELF reader finds and which finding each
defect produces. Tests that need the sample's AArch64 build are skipped,
with the reason, when it has not been built (`make sample`)."""

import copy
import os
import struct
import sys
import unittest
from pathlib import Path

from support import MUN_OS, ROOT, SAMPLE, Scratch, needs

from mdk import checks, elf, project

ENTRY = SAMPLE / "build" / "hello-mun"
PROFILE = project.profile_record("linux-arm64-gl-v0")


def elf_header(machine: int, bits: int = 64, endian: int = 1, kind: int = 2) -> bytes:
    """An ELF header and nothing else: enough to say what a file is built for."""
    ident = b"\x7fELF" + bytes([bits // 32, endian, 1, 0]) + bytes(8)
    order = "<" if endian == 1 else ">"
    return ident + struct.pack(order + "HHI", kind, machine, 1) + bytes(64 - 24)


def codes(findings):
    return [finding.code for finding in findings]


class EntryChecks(Scratch):
    def entry(self, content: bytes, name="game"):
        path = self.scratch / name
        path.write_bytes(content)
        return path

    def test_a_missing_entry(self):
        errors, _, _ = checks.check_entry(self.scratch / "nothing", PROFILE)
        self.assertEqual(codes(errors), ["E-ENTRY"])

    def test_files_that_are_not_elf_programs(self):
        for content in (b"#!/bin/sh\necho hi\n", b"MZ\x90\x00" + bytes(60), b"", b"\x7fEL"):
            errors, _, _ = checks.check_entry(self.entry(content), PROFILE)
            self.assertEqual(codes(errors), ["E-NOT-ELF"], content[:8])
        # This computer's own Python: a real program for another system (on Linux, an ELF one).
        errors, _, _ = checks.check_entry(Path(os.path.realpath(sys.executable)), PROFILE)
        self.assertTrue(errors)
        self.assertIn(errors[0].code, ("E-NOT-ELF", "E-ARCH", "E-LIB", "E-INTERP", "E-GLIBC"))

    def test_programs_for_another_machine(self):
        for machine, bits, endian, word in ((62, 64, 1, "x86-64"), (40, 32, 1, "arm"), (183, 64, 2, "big")):
            errors, _, _ = checks.check_entry(self.entry(elf_header(machine, bits, endian)), PROFILE)
            self.assertEqual(codes(errors), ["E-ARCH"])
            self.assertIn(word, errors[0].message)
            self.assertIn("ARM64", errors[0].remedy)

    def test_a_static_aarch64_program_passes(self):
        errors, warnings, facts = checks.check_entry(self.entry(elf_header(183)), PROFILE)
        self.assertEqual((errors, warnings), ([], []))
        self.assertTrue(facts["static"])
        self.assertEqual(len(facts["sha256"]), 64)

    def test_an_object_file_is_not_a_program(self):
        errors, _, _ = checks.check_entry(self.entry(elf_header(183, kind=1)), PROFILE)
        self.assertEqual(codes(errors), ["E-NOT-PROGRAM"])

    def test_an_entry_over_the_console_s_limit(self):
        small = dict(PROFILE, entry_max_bytes=32)
        errors, _, _ = checks.check_entry(self.entry(elf_header(183)), small)
        self.assertEqual(codes(errors), ["E-SIZE"])

    def test_a_truncated_elf_file_is_reported_not_trusted(self):
        header = bytearray(elf_header(183))
        struct.pack_into("<Q", header, 32, 1 << 40)      # program headers far beyond the file
        struct.pack_into("<HH", header, 54, 56, 3)
        errors, _, _ = checks.check_entry(self.entry(bytes(header)), PROFILE)
        self.assertEqual(codes(errors), ["E-NOT-ELF"])


@needs("" if ENTRY.is_file() else f"{ENTRY.relative_to(ROOT)} is not built (run `make sample`)")
class RealEntry(Scratch):
    def test_what_the_reader_finds_in_the_sample(self):
        info = elf.read(ENTRY)
        self.assertEqual((info["bits"], info["endian"], info["machine"]), (64, "little", "aarch64"))
        self.assertFalse(info["static"])
        self.assertEqual(info["interpreter"], "/lib/ld-linux-aarch64.so.1")
        self.assertIn("libSDL2-2.0.so.0", info["needed"])
        self.assertIn("libopenal.so.1", info["needed"])
        self.assertIn("libc.so.6", info["needed"])
        self.assertRegex(info["glibc"], r"^2\.\d+$")
        self.assertEqual(info["runpath"], [])

    def test_the_sample_passes_its_profile(self):
        errors, warnings, _ = checks.check_entry(ENTRY, PROFILE)
        self.assertEqual((codes(errors), codes(warnings)), ([], []))

    def test_a_library_the_profile_does_not_provide(self):
        without = copy.deepcopy(PROFILE)
        del without["libraries"]["libopenal.so.1"]
        errors, _, _ = checks.check_entry(ENTRY, without)
        self.assertEqual(codes(errors), ["E-LIB"])
        self.assertIn("libopenal.so.1", errors[0].message)
        self.assertIn("statically", errors[0].remedy)

    def test_the_framebuffer_profile_provides_neither_library(self):
        errors, _, _ = checks.check_entry(ENTRY, project.profile_record("linux-arm64-v0"))
        self.assertEqual(codes(errors), ["E-LIB", "E-LIB"])

    def test_a_newer_glibc_than_the_console_s(self):
        errors, _, _ = checks.check_entry(ENTRY, dict(PROFILE, glibc="2.17"))
        self.assertEqual(codes(errors), ["E-GLIBC"])
        self.assertIn("mdk build", errors[0].remedy)

    def test_another_loader(self):
        errors, _, _ = checks.check_entry(ENTRY, dict(PROFILE, interpreter="/lib/ld-musl-aarch64.so.1"))
        self.assertEqual(codes(errors), ["E-INTERP"])

    def test_the_whole_project(self):
        report = checks.check_project(project.load(SAMPLE, MUN_OS), MUN_OS)
        self.assertTrue(report["ok"], checks.render(report))
        self.assertEqual(report["content"]["files"], 2)


class ContentChecks(Scratch):
    def test_links_and_code_in_the_content(self):
        content = self.scratch / "content"
        (content / "sub").mkdir(parents=True)
        (content / "data.bin").write_bytes(b"data")
        (content / "sub" / "libplugin.so").write_bytes(elf_header(183, kind=3))
        os.symlink("/etc/hosts", content / "link")
        errors, warnings, facts = checks.check_content(content, "game")
        self.assertEqual(codes(errors), ["E-CONTENT-LINK"])
        self.assertEqual(codes(warnings), ["W-CODE"])
        self.assertIn("sub/libplugin.so", warnings[0].message)
        self.assertEqual(facts["files"], 2)

    def test_content_that_claims_the_entry_s_name(self):
        content = self.scratch / "content"
        content.mkdir()
        (content / "game").write_bytes(b"data")
        errors, _, _ = checks.check_content(content, "game")
        self.assertEqual(codes(errors), ["E-CONTENT-ENTRY"])

    def test_a_missing_content_directory(self):
        errors, _, _ = checks.check_content(self.scratch / "nothing", "game")
        self.assertEqual(codes(errors), ["E-CONTENT"])


class ProjectFile(Scratch):
    GOOD = ('[project]\nname = "demo"\n[card]\nid = "org.example.demo"\ntitle = "Demo"\nversion = "1.0.0"\n'
            'profile = "linux-arm64-v0"\n[build]\nrun = "make"\nentry = "build/demo"\n')

    def load(self, text):
        (self.scratch / "mun-project.toml").write_text(text, encoding="utf-8")
        return project.load(self.scratch, MUN_OS)

    def test_a_good_description(self):
        found = self.load(self.GOOD)
        self.assertEqual((found.name, found.profile["card_variant"]), ("demo", "game"))
        self.assertEqual(found.entry, self.scratch.resolve() / "build" / "demo")
        self.assertIsNone(found.content)

    def test_descriptions_that_are_refused(self):
        for text in ("", "not toml [", self.GOOD.replace('name = "demo"\n', ""), self.GOOD + '[extra]\nx = "1"\n',
                     self.GOOD.replace('run = "make"', 'run = "make"\nshell = "sh"'),
                     self.GOOD.replace("linux-arm64-v0", "windows-v9"), self.GOOD.replace('"demo"', '"de mo"')):
            with self.assertRaises(project.MdkError, msg=text[:40]):
                self.load(text)

    def test_paths_stay_inside_the_project(self):
        found = self.load(self.GOOD.replace("build/demo", "../elsewhere/demo"))
        with self.assertRaises(project.MdkError):
            found.entry

    def test_card_fields_the_console_would_refuse(self):
        (self.scratch / "build").mkdir()
        (self.scratch / "build" / "demo").write_bytes(elf_header(183))
        found = self.load(self.GOOD.replace("org.example.demo", "Bad Id").replace('"1.0.0"', '"one"'))
        report = checks.check_project(found, MUN_OS)
        self.assertEqual(codes(report["errors"]), ["E-CARD-ID", "E-CARD-VERSION"])


if __name__ == "__main__":
    unittest.main()
