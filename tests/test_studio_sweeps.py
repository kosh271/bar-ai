import json
import tempfile
import unittest
from pathlib import Path

from app import headless, studio_sweeps


class SweepLiteralTests(unittest.TestCase):
    def test_jsonc_replaces_literal_without_changing_bom_comments_or_crlf(self):
        raw = '\ufeff{\r\n  "😀": -1.5e-2, // keep 12\r\n  "name": "11",\r\n}\r\n'.encode()
        prefix = '  "😀": '
        result = studio_sweeps.replace_literal(raw, ".json", 2, len(prefix) + 2, 2.25)
        self.assertEqual(result, raw.replace(b"-1.5e-2", b"2.25"))
        for column in (len(prefix) + 14, len(prefix) + 19):
            with self.subTest(column=column), self.assertRaises(headless.MatchError):
                studio_sweeps.replace_literal(raw, ".json", 2, column, 7)

    def test_script_preserves_float_suffix_and_never_changes_a_string_or_comment(self):
        raw = b'float x = -0.5f;  // 204\nstring s = "105";\nint n = 30;'
        result = studio_sweeps.replace_literal(raw, ".as", 1, 13, 2)
        self.assertEqual(result, raw.replace(b"-0.5f", b"2.0f"))
        for line, column in ((1, 22), (2, 14), (3, 9)):
            with self.subTest(line=line), self.assertRaises(headless.MatchError):
                studio_sweeps.replace_literal(raw, ".as", line, column, 1.5)

    def test_script_does_not_treat_binary_operator_as_literal_sign(self):
        raw = b'int x = n-2;'
        self.assertEqual(studio_sweeps.replace_literal(raw, ".as", 1, 11, -3), b'int x = n- -3;')
        with self.assertRaises(headless.MatchError):
            studio_sweeps.replace_literal(raw, ".as", 1, 10, 3)

    def test_script_ignores_preprocessor_lines_and_multiline_text(self):
        raw = b'\xef\xbb\xbf #define LIMIT 42\n  #define DELAY 33\nstring s = """line\n 17\n""";'
        for line, column in ((1, 16), (2, 17), (4, 2)):
            with self.subTest(line=line), self.assertRaises(headless.MatchError):
                studio_sweeps.replace_literal(raw, ".as", line, column, 5)


class SweepMaterializationTests(unittest.TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.storage.cleanup)
        self.root = Path(self.storage.name)
        self.projects = self.root / "projects"
        self.project = self.projects / "ExampleAI"
        self.profile = self.project / "profiles" / "hard"
        (self.profile / "config").mkdir(parents=True)
        (self.profile / "script").mkdir()
        (self.project / "project.json").write_text(
            '{"name": "Example AI", "short_name": "ExampleAI", "default_profile": "hard"}', encoding="utf-8"
        )
        (self.profile / "profile.json").write_text('{"name": "Hard"}', encoding="utf-8")
        for f in ("init.as", "main.as"):
            (self.profile / "script" / f).write_text("void Entry() {}", encoding="utf-8")
        self.settings = self.profile / "config" / "settings.json"
        self.settings.write_bytes(b'{\r\n  "weight": 1, // tuning\r\n}\r\n')
        self.engine = self.root / "live-engine"
        self.stock = self.engine / "AI" / "Skirmish" / "BARb" / "stable"
        self.stock.mkdir(parents=True)
        self.dll = self.stock / "SkirmishAI.dll"
        self.dll.write_bytes(b"mock dll")
        (self.stock / "AIInfo.lua").write_text(
            "{ key = 'shortName', value = 'BARb' },\n{ key = 'version', value = 'stable' },\n"
            "{ key = 'name', value = 'BARbarIAn' },\n", encoding="utf-8"
        )
        (self.stock / "AIOptions.lua").write_text("return options\n", encoding="utf-8")
        (self.engine / "spring-headless.exe").write_bytes(b"mock headless")
        self.workspace = self.root / "operation"
        self.spec = {"project": self.project.name, "profile": "hard", "path": "config/settings.json",
                     "line": 2, "column": 13, "values": [0.5, 3]}
        self.template = headless.Player(self.project.name, "1", "hard", "Armada", 0, "Example AI")

    def test_private_runtime_and_promotion_use_exact_tested_source_after_live_edit(self):
        original = self.settings.read_bytes()
        self.workspace.mkdir()
        (self.workspace / "experiment.json").write_text("{}", encoding="utf-8")
        with studio_sweeps.prepare(self.projects, self.workspace, self.spec, self.template, self.engine) as prepared:
            self.assertNotEqual(prepared.engine_dir, self.engine)
            self.assertEqual((prepared.engine_dir / "spring-headless.exe").read_bytes(), b"mock headless")
            first, second = prepared.variants
            self.assertEqual([first.id, second.id], ["v1", "v2"])
            self.assertEqual(second.player.ally, self.template.ally)
            self.assertEqual(second.player.faction, self.template.faction)
            private = prepared.engine_dir / "AI" / "Skirmish" / second.player.short_name / second.player.version
            tested = (private / "config" / second.player.profile / "settings.json").read_bytes()
            self.assertEqual(tested, original.replace(b": 1", b": 3"))
            self.assertEqual(self.settings.read_bytes(), original)
            self.assertFalse((self.engine / "AI" / "Skirmish" / second.player.short_name).exists())
            runtime = prepared.engine_dir
            metadata = json.loads(json.dumps(prepared.promotion))
        self.assertFalse(runtime.exists())
        self.assertEqual((self.workspace / "experiment.json").read_text(), "{}")
        self.settings.write_bytes(b'{"weight": 999}')
        (self.profile / "script" / "main.as").write_text("void Changed() {}", encoding="utf-8")
        won = studio_sweeps.promote(self.projects, self.workspace, metadata, second.id, "Sweep winner")
        self.assertEqual((won / "config" / "settings.json").read_bytes(), tested)
        self.assertEqual((won / "script" / "main.as").read_text(), "void Entry() {}")
        self.assertEqual(self.settings.read_bytes(), b'{"weight": 999}')
        with self.assertRaises(headless.MatchError):
            studio_sweeps.promote(self.projects, self.workspace, metadata, second.id, "Sweep winner")

    def test_promotion_rejects_changed_snapshot_and_never_creates_partial_profile(self):
        with studio_sweeps.prepare(self.projects, self.workspace, self.spec, self.template, self.engine) as prepared:
            changed = self.workspace / "source" / "script" / "main.as"
            changed.write_text("tampered", encoding="utf-8")
            with self.assertRaises(headless.MatchError):
                studio_sweeps.promote(self.projects, self.workspace, prepared.promotion, "v1", "Bad winner")
        self.assertFalse((self.profile.parent / "bad-winner").exists())

    def test_invalid_paths_and_early_cancellation_never_create_variant_workspace(self):
        for invalid in ("../outside.json", "config/../settings.json", "C:/settings.json"):
            with self.subTest(path=invalid), self.assertRaises(headless.MatchError):
                with studio_sweeps.prepare(self.projects, self.workspace, {**self.spec, "path": invalid}, self.template, self.engine):
                    self.fail("unsafe path accepted")
        with self.assertRaises(headless.MatchError):
            with studio_sweeps.prepare(self.projects, self.workspace, self.spec, self.template, self.engine,
                                       should_stop=lambda: True):
                self.fail("cancelled preparation continued")
        self.assertFalse(self.workspace.exists())
