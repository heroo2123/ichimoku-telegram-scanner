from pathlib import Path
source=(Path(__file__).resolve().parent/'scanner_impl.py').read_text(encoding='utf-8')
exec(compile(source, str(Path(__file__).resolve().parent/'scanner_impl.py'), 'exec'), globals(), globals())
