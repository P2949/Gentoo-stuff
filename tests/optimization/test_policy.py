from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path
from typing import cast
from urllib.parse import unquote, urlsplit


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
POLICY_ROOT = REPOSITORY_ROOT / "optimization"


class OptimizationPolicyTests(unittest.TestCase):
    def load(self, name: str) -> dict[str, object]:
        value = json.loads((POLICY_ROOT / name).read_text(encoding="utf-8"))
        return cast(dict[str, object], value)

    def test_reviewed_non_suffix_shell_sources_are_regular_files(self) -> None:
        reviewed = (
            "optimization/fixtures/portage/capture-proxy.sh.in",
            "optimization/fixtures/portage/phase2-phase-identity-1.ebuild",
            "optimization/fixtures/portage/phase2-phase-identity-install-qa",
            "portage/bashrc",
            "portage/install-qa-check.d/zz-gentoo-optimization-bolt",
            "portage/repo.postsync.d/fix-sft-broken",
        )
        for relative in reviewed:
            with self.subTest(relative=relative):
                path = REPOSITORY_ROOT / relative
                self.assertTrue(path.is_file())
                self.assertFalse(path.is_symlink())

    def test_policy_has_no_unproven_active_generation(self) -> None:
        policy = self.load("policy.yaml")
        self.assertEqual(policy["schema_version"], 1)
        self.assertIsNone(policy["active_generation"])

    def test_bolt_default_is_the_exact_validated_policy(self) -> None:
        policy = self.load("policy.yaml")
        options = policy["bolt"]["default_options"]  # type: ignore[index]
        self.assertEqual(
            options,
            [
                "-reorder-blocks=ext-tsp",
                "-reorder-functions=cdsort",
                "-split-functions",
                "-split-all-cold",
                "-split-eh",
                "-icf=safe",
                "-update-debug-sections",
                "-dyno-stats",
            ],
        )
        self.assertNotIn("-use-gnu-stack", options)

    def test_review_files_start_empty(self) -> None:
        exclusions = self.load("exclusions.yaml")
        overrides = self.load("package-overrides.yaml")
        self.assertEqual(exclusions, {"schema_version": 1, "exclusions": []})
        self.assertEqual(overrides, {"schema_version": 1, "overrides": []})

    def test_repository_root_contains_only_reviewed_files(self) -> None:
        """Reject accidental shell-redirection and fixture residue at repo root."""
        reviewed = {
            ".cursor",
            ".git",
            ".github",
            ".gitignore",
            ".mypy_cache",
            ".vscode",
            "AGENTS.md",
            "CLAUDE.md",
            "GEMINI.md",
            "LICENSE",
            "README.md",
            "bench",
            "docs",
            "local-overlay",
            "optimization",
            "plan.md",
            "plans",
            "portage",
            "scripts",
            "tests",
        }
        entries = {path.name for path in REPOSITORY_ROOT.iterdir()}
        self.assertEqual(entries - reviewed, set())
        github = REPOSITORY_ROOT / ".github"
        self.assertTrue(github.is_dir())
        self.assertFalse(github.is_symlink())
        github_entries = {
            path.relative_to(github).as_posix(): (
                "symlink"
                if path.is_symlink()
                else "directory"
                if path.is_dir()
                else "file"
                if path.is_file()
                else "other"
            )
            for path in github.rglob("*")
        }
        self.assertEqual(
            github_entries,
            {
                "copilot-instructions.md": "file",
                "workflows": "directory",
                "workflows/portable-optimization-validation.yml": "file",
            },
        )
        cursor = REPOSITORY_ROOT / ".cursor"
        self.assertTrue(cursor.is_dir())
        self.assertFalse(cursor.is_symlink())
        cursor_entries = {
            path.relative_to(cursor).as_posix(): (
                "symlink"
                if path.is_symlink()
                else "directory"
                if path.is_dir()
                else "file"
                if path.is_file()
                else "other"
            )
            for path in cursor.rglob("*")
        }
        self.assertEqual(
            cursor_entries,
            {
                "rules": "directory",
                "rules/immutable-boot-kernel.mdc": "file",
            },
        )

        # Local Markdown links are part of the reviewed repository interface.
        # Resolve every inline target relative to its source file so stale
        # legacy documentation cannot silently point at a removed path.
        repository = REPOSITORY_ROOT.resolve(strict=True)
        inline_link = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")
        for markdown in sorted(REPOSITORY_ROOT.rglob("*.md")):
            if markdown.is_symlink():
                continue
            text = markdown.read_text(encoding="utf-8")
            for raw_target in inline_link.findall(text):
                parsed = urlsplit(raw_target)
                if parsed.scheme or raw_target.startswith("#") or not parsed.path:
                    continue
                relative = Path(unquote(parsed.path))
                with self.subTest(
                    markdown=markdown.relative_to(REPOSITORY_ROOT).as_posix(),
                    target=raw_target,
                ):
                    self.assertFalse(relative.is_absolute())
                    target = (markdown.parent / relative).resolve(strict=True)
                    self.assertTrue(target.is_relative_to(repository))

        history_map = (
            REPOSITORY_ROOT / "docs/commit-history-map.md"
        ).read_text(encoding="utf-8")
        for historical_commit in (
            "d8a90a41f78c20e18a83bab3f4f1a7dd418856cc",
            "19a46b78acafcc96df6a4f4c54b0880109734354",
        ):
            with self.subTest(historical_commit=historical_commit):
                self.assertIn(historical_commit, history_map)
        self.assertIn("Evidence-bearing ancestors are immutable.", history_map)
        readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Phase 2 is scope-frozen until Candidate B authorization.", readme)
        bolt_legacy = (
            REPOSITORY_ROOT / "docs/bolt-global.md"
        ).read_text(encoding="utf-8")
        self.assertIn("The retired prototype made packages BOLT-ready globally", bolt_legacy)
        self.assertIn("The prototype is permanently disabled.", bolt_legacy)
        self.assertNotIn("This repository makes packages BOLT-ready globally", bolt_legacy)
        self.assertNotRegex(bolt_legacy, r"(?m)^```(?:bash|sh|shell)?$")
        self.assertNotRegex(bolt_legacy, r"/opt/bolt-test|/var/tmp/bolt-profiles")

        for wrapper_name, command in (
            ("capture-input.sh", "capture"),
            ("deploy-output.sh", "deploy"),
            ("register-output.sh", "register-output"),
        ):
            wrapper = (
                REPOSITORY_ROOT / "scripts/optimization/bolt" / wrapper_name
            ).read_text(encoding="utf-8")
            with self.subTest(wrapper=wrapper_name):
                self.assertIn(
                    'exec /usr/bin/python3 -I -B "${SCRIPT_DIR}/artifact_tool.py" '
                    f'{command} "$@"',
                    wrapper,
                )
                self.assertNotIn(
                    'exec /usr/bin/python3 -I "${SCRIPT_DIR}/artifact_tool.py"',
                    wrapper,
                )

    def test_portable_ci_actions_are_pinned_to_immutable_commits(self) -> None:
        workflow = (
            REPOSITORY_ROOT
            / ".github/workflows/portable-optimization-validation.yml"
        ).read_text(encoding="utf-8")
        uses = re.findall(r"^\s*uses:\s*([^\s#]+)", workflow, re.MULTILINE)
        self.assertEqual(len(uses), 2)
        for reference in uses:
            self.assertRegex(reference, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
        self.assertEqual(
            re.findall(r"^\s*timeout-minutes:\s*([0-9]+)\s*$", workflow, re.MULTILINE),
            ["75"],
        )
        for required_fragment in (
            "test -x /usr/sbin/runuser",
            "grep -Fx 'util-linux: /usr/sbin/runuser'",
            "sudo ln --symbolic -- /usr/sbin/runuser /usr/bin/runuser",
            "readlink --canonicalize-existing /usr/bin/runuser",
            "^PASS[[:space:]]+framework-installer[[:space:]]+",
        ):
            with self.subTest(required_fragment=required_fragment):
                self.assertIn(required_fragment, workflow)

    def test_phase2_evidence_binds_checkpoint_and_runtime_primitives(self) -> None:
        policy = self.load("phase2-evidence-policy.json")
        manifest = self.load("phase2-tool-manifest.json")
        required_tools = cast(list[str], policy["required_tools"])
        test_execution_tools = cast(list[str], policy["test_execution_tools"])
        manifest_tools = cast(list[dict[str, object]], manifest["tools"])
        manifest_names = [cast(str, entry["name"]) for entry in manifest_tools]
        checkpoint_primitives = {
            "cat",
            "date",
            "df",
            "du",
            "emaint",
            "emerge",
            "findmnt",
            "mount",
            "qcheck",
            "qsize",
            "quickpkg",
            "sleep",
            "umount",
            "wc",
            "zstd",
        }
        prerequisite_primitives = {
            "bash-bootstrap",
            "cargo",
            "cp",
            "emerge-python3.15",
            "false",
            "gemato",
            "gemato-python3.15",
            "git",
            "gpep517-python3.15",
            "gpg",
            "gpgconf",
            "ldconfig",
            "maturin",
            "meson-python3.14",
            "mount",
            "ninja",
            "python3.14",
            "python3.15",
            "qcheck",
            "rustc",
            "umount",
            "unshare",
            "wget",
            "zstd",
        }
        native_primitives = {
            "clang",
            "clang-cpp",
            "clang-cxx",
            "ld-lld",
            "llvm-ar",
            "llvm-nm",
            "llvm-ranlib",
            "llvm-strip",
            "pkg-config",
        }
        framework_primitives = {"systemd-tmpfiles"}
        operator_primitives = {"cut", "doas"}
        self.assertEqual(required_tools, sorted(required_tools))
        self.assertEqual(manifest_names, required_tools)
        self.assertEqual(
            policy["authoritative_test_path"],
            ["/usr/bin", "/usr/lib/llvm/22/bin", "/bin"],
        )
        self.assertEqual(
            test_execution_tools,
            [
                "bash",
                "env",
                "git",
                "python3",
                "setsid",
                "shellcheck",
                "sleep",
                "timeout",
            ],
        )
        self.assertLessEqual(set(test_execution_tools), set(required_tools))
        self.assertLessEqual(
            checkpoint_primitives
            | prerequisite_primitives
            | native_primitives
            | framework_primitives
            | operator_primitives,
            set(required_tools),
        )
        manifest_by_name = {
            cast(str, entry["name"]): entry for entry in manifest_tools
        }
        self.assertEqual(
            manifest_by_name["cut"],
            {
                "name": "cut",
                "path": "/usr/bin/cut",
                "version_args": ["--version"],
            },
        )
        self.assertEqual(
            manifest_by_name["doas"],
            {
                "name": "doas",
                "path": "/usr/bin/doas",
                "version_args": ["-n", "/usr/bin/id", "-u"],
            },
        )
        self.assertEqual(
            manifest_by_name["false"],
            {
                "name": "false",
                "path": "/usr/bin/false",
                "version_args": ["--version"],
                "version_returncodes": [1],
            },
        )
        prerequisite_tool_rows = {
            "cargo": {
                "name": "cargo",
                "path": "/usr/bin/cargo",
                "version_args": ["--version"],
            },
            "clang-cpp": {
                "name": "clang-cpp",
                "path": "/usr/lib/llvm/22/bin/clang-cpp",
                "version_args": ["--version"],
            },
            "clang-cxx": {
                "name": "clang-cxx",
                "path": "/usr/lib/llvm/22/bin/clang++",
                "version_args": ["--version"],
            },
            "emerge-python3.15": {
                "name": "emerge-python3.15",
                "path": "/usr/lib/python-exec/python3.15/emerge",
                "version_args": ["--version"],
            },
            "gemato-python3.15": {
                "name": "gemato-python3.15",
                "path": "/usr/lib/python-exec/python3.15/gemato",
                "version_args": ["--help"],
            },
            "gpep517-python3.15": {
                "name": "gpep517-python3.15",
                "path": "/usr/lib/python-exec/python3.15/gpep517",
                "version_args": ["--help"],
            },
            "ld-lld": {
                "name": "ld-lld",
                "path": "/usr/lib/llvm/22/bin/ld.lld",
                "version_args": ["--version"],
            },
            "llvm-ar": {
                "name": "llvm-ar",
                "path": "/usr/lib/llvm/22/bin/llvm-ar",
                "version_args": ["--version"],
            },
            "llvm-nm": {
                "name": "llvm-nm",
                "path": "/usr/lib/llvm/22/bin/llvm-nm",
                "version_args": ["--version"],
            },
            "llvm-ranlib": {
                "name": "llvm-ranlib",
                "path": "/usr/lib/llvm/22/bin/llvm-ranlib",
                "version_args": ["--version"],
            },
            "llvm-strip": {
                "name": "llvm-strip",
                "path": "/usr/lib/llvm/22/bin/llvm-strip",
                "version_args": ["--version"],
            },
            "maturin": {
                "name": "maturin",
                "path": "/usr/bin/maturin",
                "version_args": ["--version"],
            },
            "meson-python3.14": {
                "name": "meson-python3.14",
                "path": "/usr/lib/python-exec/python3.14/meson",
                "version_args": ["--version"],
            },
            "ninja": {
                "name": "ninja",
                "path": "/usr/bin/ninja",
                "version_args": ["--version"],
            },
            "pkg-config": {
                "name": "pkg-config",
                "path": "/usr/bin/pkg-config",
                "version_args": ["--version"],
            },
            "python3.14": {
                "name": "python3.14",
                "path": "/usr/bin/python3.14",
                "version_args": ["--version"],
            },
        }
        self.assertEqual(
            {
                name: manifest_by_name[name]
                for name in prerequisite_tool_rows
            },
            prerequisite_tool_rows,
        )

        helper_path = (
            REPOSITORY_ROOT
            / "scripts/optimization/recovery/install-jsonschema-prerequisite.py"
        )
        helper_source = helper_path.read_text(encoding="utf-8")
        helper_tree = ast.parse(helper_source, filename=str(helper_path))
        default_tools_functions = [
            node
            for node in helper_tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "default_tools"
        ]
        self.assertEqual(len(default_tools_functions), 1)
        default_returns = [
            node
            for node in default_tools_functions[0].body
            if isinstance(node, ast.Return)
        ]
        self.assertEqual(len(default_returns), 1)
        default_value = default_returns[0].value
        self.assertIsInstance(default_value, ast.Dict)
        assert isinstance(default_value, ast.Dict)
        helper_external_tools: dict[str, str] = {}
        source_bound_tools: set[str] = set()
        for key_node, value_node in zip(
            default_value.keys, default_value.values, strict=True
        ):
            self.assertIsInstance(key_node, ast.Constant)
            assert isinstance(key_node, ast.Constant)
            self.assertIsInstance(key_node.value, str)
            name = cast(str, key_node.value)
            if (
                isinstance(value_node, ast.Call)
                and isinstance(value_node.func, ast.Name)
                and value_node.func.id == "tool"
                and len(value_node.args) == 1
                and isinstance(value_node.args[0], ast.Constant)
                and isinstance(value_node.args[0].value, str)
            ):
                helper_external_tools[name] = value_node.args[0].value
            else:
                source_bound_tools.add(name)
        self.assertEqual(
            helper_external_tools,
            {
                "bash": "/bin/bash",
                "cargo": "/usr/bin/cargo",
                "cp": "/usr/bin/cp",
                "emerge": "/usr/lib/python-exec/python3.15/emerge",
                "false": "/usr/bin/false",
                "gemato": "/usr/lib/python-exec/python3.15/gemato",
                "git": "/usr/bin/git",
                "gpep517": "/usr/lib/python-exec/python3.15/gpep517",
                "gpg": "/usr/bin/gpg",
                "gpgconf": "/usr/bin/gpgconf",
                "ldconfig": "/usr/bin/ldconfig",
                "maturin": "/usr/bin/maturin",
                "meson": "/usr/lib/python-exec/python3.14/meson",
                "meson_python": "/usr/bin/python3.14",
                "mount": "/usr/bin/mount",
                "ninja": "/usr/bin/ninja",
                "python": "/usr/bin/python3.15",
                "qcheck": "/usr/bin/qcheck",
                "rustc": "/usr/bin/rustc",
                "sync": "/usr/bin/sync",
                "umount": "/usr/bin/umount",
                "unshare": "/usr/bin/unshare",
                "wget": "/usr/bin/wget",
                "zstd": "/usr/bin/zstd",
            },
        )
        self.assertEqual(
            source_bound_tools, {"snapshot_verifier", "transaction"}
        )

        assignments = {
            node.target.id: ast.literal_eval(node.value)
            for node in helper_tree.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id
            in {"BUILD_VERSION_ARGUMENTS", "NATIVE_BUILD_COMMAND_DEFAULTS"}
            and node.value is not None
        }
        self.assertEqual(
            assignments["NATIVE_BUILD_COMMAND_DEFAULTS"],
            {
                "AR": "ar",
                "CC": "cc",
                "CPP": "cpp",
                "CXX": "c++",
                "LD": "ld",
                "NM": "nm",
                "PKG_CONFIG": "pkg-config",
                "RANLIB": "ranlib",
                "STRIP": "strip",
            },
        )
        self.assertEqual(
            assignments["BUILD_VERSION_ARGUMENTS"],
            {
                "cargo": ("--version",),
                "emerge": ("--version",),
                "gpep517": ("--help",),
                "meson": ("--version",),
                "maturin": ("--version",),
                "ninja": ("--version",),
                "python": ("--version",),
                "rustc": ("-vV",),
            },
        )
        build_manifest_names = {
            "cargo": "cargo",
            "emerge": "emerge-python3.15",
            "gpep517": "gpep517-python3.15",
            "meson": "meson-python3.14",
            "maturin": "maturin",
            "ninja": "ninja",
            "python": "python3.15",
            "rustc": "rustc",
        }
        for helper_name, manifest_name in build_manifest_names.items():
            with self.subTest(build_tool=helper_name):
                self.assertEqual(
                    manifest_by_name[manifest_name]["version_args"],
                    list(assignments["BUILD_VERSION_ARGUMENTS"][helper_name]),
                )
                self.assertNotIn(
                    "version_returncodes", manifest_by_name[manifest_name]
                )
        native_manifest_names = {
            "AR": "llvm-ar",
            "CC": "clang",
            "CPP": "clang-cpp",
            "CXX": "clang-cxx",
            "LD": "ld-lld",
            "NM": "llvm-nm",
            "PKG_CONFIG": "pkg-config",
            "RANLIB": "llvm-ranlib",
            "STRIP": "llvm-strip",
        }
        self.assertEqual(
            {
                variable: cast(str, manifest_by_name[name]["path"])
                for variable, name in native_manifest_names.items()
            },
            {
                "AR": "/usr/lib/llvm/22/bin/llvm-ar",
                "CC": "/usr/lib/llvm/22/bin/clang",
                "CPP": "/usr/lib/llvm/22/bin/clang-cpp",
                "CXX": "/usr/lib/llvm/22/bin/clang++",
                "LD": "/usr/lib/llvm/22/bin/ld.lld",
                "NM": "/usr/lib/llvm/22/bin/llvm-nm",
                "PKG_CONFIG": "/usr/bin/pkg-config",
                "RANLIB": "/usr/lib/llvm/22/bin/llvm-ranlib",
                "STRIP": "/usr/lib/llvm/22/bin/llvm-strip",
            },
        )
        for variable, manifest_name in native_manifest_names.items():
            with self.subTest(native_axis=variable):
                self.assertEqual(
                    manifest_by_name[manifest_name]["version_args"],
                    ["--version"],
                )
                self.assertNotIn(
                    "version_returncodes", manifest_by_name[manifest_name]
                )

        # Safety-critical executable references are closed mechanically over
        # the reviewed manifest.  The runbook uses absolute commands only;
        # the helper's default tool table and bootstrap publisher are scanned
        # alongside it, so adding a new executable without tool authority is
        # a deterministic policy failure rather than another assertIn list.
        command_sources = "\n".join(
            (
                (
                    REPOSITORY_ROOT / "docs/binpkg-checkpoint-runbook.md"
                ).read_text(encoding="utf-8"),
                (
                    REPOSITORY_ROOT
                    / "scripts/optimization/recovery/install-jsonschema-prerequisite.py"
                ).read_text(encoding="utf-8"),
                (
                    REPOSITORY_ROOT
                    / "scripts/optimization/recovery/publish-jsonschema-prerequisite-bootstrap.py"
                ).read_text(encoding="utf-8"),
            )
        )
        executable_path_pattern = re.compile(
            r"(?<![A-Za-z0-9_.-])"
            r"(/(?:(?:usr/)?(?:s?bin|bin)|"
            r"usr/lib/python-exec/python[0-9]+(?:\.[0-9]+)+|"
            r"usr/lib/llvm/[0-9]+/bin)/"
            r"[A-Za-z0-9][A-Za-z0-9._+-]*)"
        )
        referenced_executables = set(executable_path_pattern.findall(command_sources))
        reviewed_executables = {
            cast(str, entry["path"]) for entry in manifest_tools
        }
        referenced_executables = {
            "/usr/bin/false" if path == "/bin/false" else path
            for path in referenced_executables
        }
        self.assertEqual(referenced_executables - reviewed_executables, set())
        self.assertLessEqual(
            set(helper_external_tools.values()), reviewed_executables
        )
        # The generic python-exec dispatchers remain reviewed for other Phase
        # 2 paths, but they cannot stand in for this transaction's selected
        # implementations.
        self.assertNotEqual(
            manifest_by_name["emerge"]["path"],
            manifest_by_name["emerge-python3.15"]["path"],
        )
        self.assertNotEqual(
            manifest_by_name["gemato"]["path"],
            manifest_by_name["gemato-python3.15"]["path"],
        )

        runbook_bash = "\n".join(
            re.findall(
                r"```bash\n(.*?)```",
                (
                    REPOSITORY_ROOT / "docs/binpkg-checkpoint-runbook.md"
                ).read_text(encoding="utf-8"),
                re.DOTALL,
            )
        )
        self.assertNotRegex(
            runbook_bash,
            r"(?m)(?:^|[|;(]\s*|\$\()"
            r"(?:awk|cat|comm|cut|df|du|find|findmnt|grep|jq|qsize|"
            r"readlink|sha256sum|sort|stat|wc)(?=\s)",
        )

        production_runbook = (
            REPOSITORY_ROOT / "docs/phase2-production-profile-transaction.md"
        ).read_text(encoding="utf-8")
        materialization_boundary = production_runbook.split(
            "## Create the candidate's immutable source snapshot", 1
        )[1].split(
            "Also prove containment and recover any earlier interrupted transaction",
            1,
        )[0]
        for required_fragment in (
            "PATH=/usr/bin:/bin",
            "/usr/bin/cut",
            "/usr/bin/doas",
            "/usr/bin/git",
            "/usr/bin/timeout --signal=TERM",
            "[[ ! -e $1 && ! -L $1 ]]",
        ):
            with self.subTest(required_fragment=required_fragment):
                self.assertIn(required_fragment, materialization_boundary)
        self.assertGreaterEqual(
            materialization_boundary.count("/usr/bin/timeout --signal=TERM"), 2
        )
        self.assertNotRegex(
            materialization_boundary,
            r"(?m)^\s*(?:doas|git|test|cut|date|sha256sum|awk)\b",
        )
        self.assertNotRegex(materialization_boundary, r"\|\s*(?:awk|cut)\b")

        checkpoint_runbook = (
            REPOSITORY_ROOT / "docs/binpkg-checkpoint-runbook.md"
        ).read_text(encoding="utf-8")
        # A failed command substitution can yield an empty string.  Wrapping
        # it directly in `test -z` therefore turns command failure into
        # apparent success under set -e.  Safety-critical observations must
        # capture and check command status before testing an empty result.
        self.assertNotRegex(
            checkpoint_runbook,
            r'''test\s+-z\s+["']\$\(''',
        )

        installer_source = (
            REPOSITORY_ROOT / "scripts/optimization/install-framework.sh"
        ).read_text(encoding="utf-8")
        installer_fixture = (
            REPOSITORY_ROOT / "tests/optimization/test-framework-installer.sh"
        ).read_text(encoding="utf-8")
        exchange_adapter = (
            REPOSITORY_ROOT
            / "tests/optimization/fixtures/rename-exchange-mv.py"
        )
        self.assertIn(
            "fixture atomic-exchange tool override is forbidden in production",
            installer_source,
        )
        self.assertIn("EXCHANGE_TOOL=/usr/bin/mv", installer_source)
        self.assertIn(
            'GENTOO_OPT_INSTALLER_TEST_EXCHANGE_TOOL="${EXCHANGE_TOOL}"',
            installer_source,
        )
        self.assertIn(
            "GENTOO_OPT_INSTALLER_TEST_EXCHANGE_TOOL=${EXCHANGE_TOOL}",
            installer_fixture,
        )
        self.assertTrue(exchange_adapter.is_file())
        self.assertFalse(exchange_adapter.is_symlink())
        self.assertTrue(exchange_adapter.stat().st_mode & 0o111)

        required_sources = set(cast(list[str], policy["required_sources"]))
        legacy_bolt_sources = {
            "docs/bolt-global.md",
            "scripts/bolt/bolt-package-binaries.sh",
            "scripts/bolt/collect-profile.sh",
            "scripts/bolt/list-package-binaries.sh",
            "scripts/bolt/optimize-binary.sh",
            "tests/optimization/test-bolt-command-policy.sh",
            "tests/optimization/test-no-legacy-bolt.sh",
        }
        self.assertLessEqual(
            {
                "docs/binpkg-checkpoint-runbook.md",
                "optimization/tmpfiles/gentoo-optimization.conf",
                "scripts/optimization/recovery/create-binpkg-checkpoint.sh",
                "scripts/optimization/recovery/install-jsonschema-prerequisite.py",
                "scripts/optimization/recovery/publish-jsonschema-prerequisite-bootstrap.py",
                "scripts/optimization/recovery/verify-binpkg-snapshot.py",
                "tests/optimization/fixtures/rename-exchange-mv.py",
                "tests/optimization/recovery/test_create_binpkg_checkpoint.py",
                "tests/optimization/test_jsonschema_prerequisite.py",
                "tests/optimization/test_jsonschema_prerequisite_bootstrap.py",
            },
            required_sources,
        )
        self.assertLessEqual(legacy_bolt_sources, required_sources)
        self.assertIn("scripts/bolt", cast(list[str], policy["source_scopes"]))
        self.assertIn(
            "no-legacy-bolt",
            cast(list[str], policy["required_passing_test_names"]),
        )

        claims = {
            cast(str, claim["claim_id"]): set(
                cast(list[str], claim["source_paths"])
            )
            for claim in cast(list[dict[str, object]], policy["plan_claims"])
        }
        self.assertLessEqual(
            {
                "docs/binpkg-checkpoint-runbook.md",
                "scripts/optimization/recovery/create-binpkg-checkpoint.sh",
                "scripts/optimization/recovery/install-jsonschema-prerequisite.py",
                "scripts/optimization/recovery/publish-jsonschema-prerequisite-bootstrap.py",
                "scripts/optimization/recovery/verify-binpkg-snapshot.py",
                "tests/optimization/recovery/test_create_binpkg_checkpoint.py",
                "tests/optimization/test_jsonschema_prerequisite.py",
                "tests/optimization/test_jsonschema_prerequisite_bootstrap.py",
            },
            claims["phase2-automation"],
        )
        self.assertLessEqual(legacy_bolt_sources, claims["phase2-bolt-hooks"])
        bolt_component = next(
            component
            for component in cast(
                list[dict[str, object]], policy["required_component_states"]
            )
            if component["name"] == "bolt-hooks"
        )
        self.assertIn(
            "no-legacy-bolt",
            cast(list[str], bolt_component["required_test_names"]),
        )
        self.assertIn(
            "optimization/tmpfiles/gentoo-optimization.conf",
            claims["phase2-framework"],
        )
        self.assertIn(
            "tests/optimization/fixtures/rename-exchange-mv.py",
            claims["phase2-framework"],
        )

        checkpoint_runbook = (
            REPOSITORY_ROOT / "docs/binpkg-checkpoint-runbook.md"
        ).read_text(encoding="utf-8")
        for required_fragment in (
            "CHECKOUT_SOURCE=/var/lib/gentoo-optimization/bootstrap/source-checkouts/",
            "BINPKG_SOURCE=$(/usr/bin/readlink -e /var/cache/gentoo-optimization/binpkgs/critical-current)",
            '"$PREREQUISITE_PUBLISHER_SOURCE" publish',
            "gentoo-optimization-jsonschema-prerequisite-bootstrap-v1",
            "VERIFIED_PREREQUISITE_BOOTSTRAP=",
            "LIVE_PREPARATION_ENABLED",
            "LIVE_MUTATION_ENABLED",
            "stop before prepare",
            '"${PREREQUISITE_EXEC[@]}" prepare "$INSTALL_ID"',
            '"${PREREQUISITE_EXEC[@]}" run "$INSTALL_ID"',
            '"${PREREQUISITE_EXEC[@]}" recover "$INSTALL_ID"',
            '"${PREREQUISITE_EXEC[@]}" verify "$INSTALL_ID"',
            "INSTALL_ATTEMPT=/var/lib/gentoo-optimization/state/project/",
            "start a fresh clean root shell exactly as",
            "Do not rerun `publish`, `prepare`, or",
            "accepts only the four public commands `prepare`, `run`,",
            ".phase == \"success\"",
            "POST_CHECKPOINT_ID=post-candidate-a-jsonschema-",
            "expected-delta-atoms.txt",
            "JSONSCHEMA_RESTORE_CPVS",
            "publish_checkpoint_operator_evidence \"$POST_CHECKPOINT_ID\" \"$EVIDENCE\"",
            "/root/checkpoint-evidence-ID",
            "/var/lib/gentoo-optimization/reports/checkpoint-ID-operator-evidence",
        ):
            with self.subTest(required_fragment=required_fragment):
                self.assertIn(required_fragment, checkpoint_runbook)
        for retired_fragment in (
            "SOURCE_EMERGE_COMMAND",
            "capture_ebuild_provenance",
            "installed-cpvs.before.txt",
            "from portage.getbinpkg import PackageIndex",
            "repository_commit",
        ):
            with self.subTest(retired_fragment=retired_fragment):
                self.assertNotIn(retired_fragment, checkpoint_runbook)
        self.assertNotRegex(checkpoint_runbook, r"(?m)^SOURCE=")
        self.assertNotIn("force_reindex=True", checkpoint_runbook)
        self.assertGreaterEqual(
            checkpoint_runbook.count("--finalize-offline-restore"), 5
        )
        self.assertLess(
            checkpoint_runbook.index("PRE_CHECKPOINT_TERMINAL=$CHECKPOINT_RESTORED_STATE"),
            checkpoint_runbook.index("PREREQUISITE_PUBLISHER_SOURCE="),
        )
        self.assertLess(
            checkpoint_runbook.index("PREREQUISITE_GATE=$("),
            checkpoint_runbook.index("PREREQUISITE_BOOTSTRAP=$("),
        )
        self.assertLess(
            checkpoint_runbook.index("PREREQUISITE_GATE=$("),
            checkpoint_runbook.index("INSTALL_ID=jsonschema-source-"),
        )
        self.assertLess(
            checkpoint_runbook.index('"${PREREQUISITE_EXEC[@]}" run "$INSTALL_ID"'),
            checkpoint_runbook.index(
                "POST_CHECKPOINT_ID=post-candidate-a-jsonschema-"
            ),
        )

        publisher_source = (
            REPOSITORY_ROOT
            / "scripts/optimization/recovery/publish-jsonschema-prerequisite-bootstrap.py"
        ).read_text(encoding="utf-8")
        for invariant in (
            'git_bytes(repository, "ls-tree", "-z", "HEAD"',
            'git_bytes(repository, "cat-file", "blob"',
            "worktree source differs byte-for-byte from HEAD blob",
            "status = git_command(",
            '"--ignored=matching"',
            "validate_tree(authority.parent, Path(\"/\"), 0, 0)",
            "expected_mode = 0o755 if authority.production else 0o700",
            "validate_public_helper_command(command)",
            "ABSOLUTE_CANONICAL_PATH",
            "RENAME_NOREPLACE",
            "fsync_directory(stage)",
            "fsync_directory(parent)",
            "exec must be invoked through the published bootstrap publisher",
            "verify must be invoked through the published bootstrap publisher",
        ):
            with self.subTest(publisher_invariant=invariant):
                self.assertIn(invariant, publisher_source)


if __name__ == "__main__":
    unittest.main()
