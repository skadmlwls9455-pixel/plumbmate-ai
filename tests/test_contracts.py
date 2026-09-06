"""Executable wire contracts: package resources, providers, and exported docs."""

import ast
import copy
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

from plumbmate.cli import make_demo_request
from plumbmate.contracts import (
    REQUEST_SCHEMA, RESPONSE_SCHEMA, ProtocolError, model_output_schema,
    validate_model_output, validate_request, validate_response,
)
from plumbmate.providers import MockProvider, RESPONSE_SCHEMA as PROVIDER_SCHEMA

ROOT = Path(__file__).resolve().parents[1]


class ContractTests(unittest.TestCase):
    def test_exported_schemas_are_exact_canonical_copies(self):
        subprocess.run([sys.executable, str(ROOT / "scripts/export_contracts.py"), "--check"], check=True)
        self.assertEqual(json.loads((ROOT / "protocol/request.schema.json").read_text()), REQUEST_SCHEMA)
        self.assertEqual(json.loads((ROOT / "protocol/response.schema.json").read_text()), RESPONSE_SCHEMA)

    def test_provider_schema_is_derived_and_cannot_change_contract(self):
        self.assertEqual(PROVIDER_SCHEMA, model_output_schema())
        edited = model_output_schema()
        edited["properties"]["action"]["properties"]["name"]["enum"].append("escape")
        self.assertNotIn("escape", model_output_schema()["properties"]["action"]["properties"]["name"]["enum"])

    def test_model_cannot_supply_transport_identifiers(self):
        response = MockProvider().respond(make_demo_request("hello"))
        generated = {key: response[key] for key in model_output_schema()["properties"]}
        validate_model_output(generated)
        for key in ("request_id", "protocol_version", "budget", "memory_policy"):
            with self.subTest(key=key), self.assertRaises(ProtocolError):
                validate_model_output(dict(generated, **{key: "forged"}))

    def test_nested_types_and_extra_fields_are_rejected_without_echoing_values(self):
        response = MockProvider().respond(make_demo_request("hello"))
        for change in ({"name": []}, {"arguments": []}, {"reason": 3}, {"secret": "PRIVATE_MARKER"}):
            altered = copy.deepcopy(response)
            altered["action"].update(change)
            with self.subTest(change=change), self.assertRaises(ProtocolError) as caught:
                validate_response(altered)
            self.assertNotIn("PRIVATE_MARKER", str(caught.exception))
        for value in (None, [], 1, "text"):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                validate_request(value)

    def test_all_game_sources_remain_python_37_syntax_and_no_companion_imports(self):
        for source in (ROOT / "game_mod/src").rglob("*.py"):
            with self.subTest(source=source.name):
                tree = ast.parse(source.read_text(), feature_version=(3, 7))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        self.assertFalse(any(alias.name.split(".")[0] in {"plumbmate", "jsonschema"} for alias in node.names))
                    if isinstance(node, ast.ImportFrom):
                        self.assertNotIn((node.module or "").split(".")[0], {"plumbmate", "jsonschema"})

    def test_build_manifest_covers_root_level_production_imports(self):
        build = ast.parse((ROOT / "game_mod/build_game_mod.py").read_text())
        manifest = {node.value for node in ast.walk(build)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and node.value.endswith(".pyc")}
        source = ast.parse((ROOT / "game_mod/src/plumbmate_mod/commands.py").read_text())
        imports = {alias.name + ".pyc" for node in ast.walk(source) if isinstance(node, ast.Import)
                   for alias in node.names if alias.name.startswith("plumbmate_")}
        self.assertTrue(imports)
        self.assertTrue(imports <= manifest, imports - manifest)

    def test_diagnostic_path_helpers_preserve_unicode_indices(self):
        for filename, helper in (("diagnostics.py", "_diagnostic_directory"), ("ownership_probe.py", "_root")):
            tree = ast.parse((ROOT / "game_mod/src/plumbmate_mod" / filename).read_text())
            function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == helper)
            # Exercise pure path helper without importing diagnostic EA hooks.
            scope = {"os": os, "re": re, "__file__": "/Users/İ/Mods/Probe.TS4SCRIPT/probe.pyc"}
            exec(compile(ast.Module(body=[function], type_ignores=[]), filename, "exec"), scope)
            with self.subTest(filename=filename):
                self.assertEqual(scope[helper](), "/Users/İ/Mods/PlumbMateDiagnostics")
