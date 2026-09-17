"""Build a game-compatible .ts4script with The Sims 4's Python runtime."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
import zipfile
from pathlib import Path
from pathlib import PurePosixPath


DEFAULT_GAME_APP = Path("/Applications/EA Games/The Sims 4.app")


def _game_paths(game_app: Path) -> tuple[Path, Path, Path]:
    contents = game_app / "Contents"
    library = contents / "Frameworks" / "libpython370.dylib"
    base_zip = contents / "Data" / "Simulation" / "Gameplay" / "base.zip"
    core_zip = contents / "Data" / "Simulation" / "Gameplay" / "core.zip"
    missing = [path for path in (library, base_zip, core_zip) if not path.is_file()]
    if missing:
        raise RuntimeError("Missing game runtime files: {}".format(", ".join(map(str, missing))))
    return library, base_zip, core_zip


def _game_magic(core_zip: Path) -> bytes:
    with zipfile.ZipFile(core_zip) as archive:
        return archive.read("sims4/commands.pyc")[:4]


def _build_compiler(target: Path) -> None:
    source = Path(__file__).resolve().with_name("compile_with_game_python.c")
    subprocess.run(
        ["cc", "-std=c11", "-O2", str(source), "-o", str(target)],
        check=True,
    )


def build(output: Path, game_app: Path = DEFAULT_GAME_APP, *, diagnostic: bool = False) -> Path:
    source_root = Path(__file__).resolve().parent / "src"
    entry_source = source_root / "plumbmate_mod" / (
        "diagnostics.py" if diagnostic else "commands.py"
    )
    if not entry_source.is_file():
        raise RuntimeError("Game-mod entry source not found")
    # The current TS4 importer reliably discovers root-level bytecode modules.
    # Keep the source organized as a package, but publish one explicit boot module.
    compile_units = [(entry_source, Path(
        "plumbmate_diagnostics.pyc" if diagnostic else "plumbmate_boot.pyc"
    ))]
    if diagnostic:
        compile_units.extend([
            (source_root / "plumbmate_mod" / "probe_core.py", Path("plumbmate_probe_core.pyc")),
            (source_root / "plumbmate_mod" / "ownership_probe.py", Path("plumbmate_ownership_probe.pyc")),
            (source_root / "plumbmate_mod" / "socket_probe.py", Path("plumbmate_socket_probe.pyc")),
        ])
    else:
        # commands.py imports these by their published root-level names.
        compile_units.extend([
            (source_root / "plumbmate_mod" / "bridge_client.py", Path("plumbmate_bridge_client.pyc")),
            (source_root / "plumbmate_mod" / "tick_driver.py", Path("plumbmate_tick_driver.pyc")),
            (source_root / "plumbmate_mod" / "turn_bridge.py", Path("plumbmate_turn_bridge.pyc")),
            (source_root / "plumbmate_mod" / "role_binding.py", Path("plumbmate_role_binding.pyc")),
            (source_root / "plumbmate_mod" / "role_setup_ui.py", Path("plumbmate_role_setup_ui.pyc")),
            (source_root / "plumbmate_mod" / "chat_input.py", Path("plumbmate_chat_input.pyc")),
            (source_root / "plumbmate_mod" / "chat_ui.py", Path("plumbmate_chat_ui.pyc")),
            (source_root / "plumbmate_mod" / "control_ui.py", Path("plumbmate_control_ui.pyc")),
            (source_root / "plumbmate_mod" / "reply_delivery.py", Path("plumbmate_reply_delivery.pyc")),
        ])

    library, base_zip, core_zip = _game_paths(game_app)
    expected_magic = _game_magic(core_zip)
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="plumbmate-build-") as temporary:
        temporary_root = Path(temporary)
        compiler = temporary_root / "game-python-compile"
        compiled_root = temporary_root / "compiled"
        runtime_root = temporary_root / "runtime"
        with zipfile.ZipFile(base_zip) as archive:
            for member in archive.infolist():
                member_path = PurePosixPath(member.filename)
                if member.is_dir() or not member_path.parts or member_path.parts[0] != "lib":
                    continue
                relative = Path(*member_path.parts[1:])
                target = runtime_root / "lib" / "python3.7" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))
        _build_compiler(compiler)

        compiled: list[tuple[Path, str]] = []
        for source, relative in compile_units:
            target = compiled_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                [
                    str(compiler),
                    str(library),
                    str(runtime_root),
                    str(source),
                    str(target),
                    relative.as_posix(),
                ],
                check=True,
            )
            if target.read_bytes()[:4] != expected_magic:
                raise RuntimeError("Compiled bytecode does not match the installed game runtime")
            compiled.append((target, relative.as_posix()))

        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for target, relative in compiled:
                archive.write(target, relative)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "dist" / "PlumbMate.ts4script",
    )
    parser.add_argument("--game-app", type=Path, default=DEFAULT_GAME_APP)
    parser.add_argument("--diagnostic", action="store_true",
                        help="Build the separate opt-in development probe")
    args = parser.parse_args()
    if args.diagnostic and args.output.name == "PlumbMate.ts4script":
        parser.error("Use a separate --output PlumbMateDiagnostics.ts4script")
    print(build(args.output.resolve(), args.game_app.resolve(), diagnostic=args.diagnostic))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
