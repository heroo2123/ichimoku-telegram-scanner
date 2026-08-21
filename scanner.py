from pathlib import Path

source = (Path(__file__).resolve().parent / "scanner_impl.py").read_text(encoding="utf-8")
main_guard = "\nif __name__ == '__main__':"
if main_guard not in source:
    raise RuntimeError("scanner_impl.py main guard was not found")
source = source.rsplit(main_guard, 1)[0]
exec(compile(source, str(Path(__file__).resolve().parent / "scanner_impl.py"), "exec"), globals(), globals())

from v3.benchmark_digest import install as _install_benchmark_digest

_install_benchmark_digest(globals())

if __name__ == "__main__":
    raise SystemExit(main())
