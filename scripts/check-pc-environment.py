"""Read-only installed-version and critical-import checks; no service/inference runs."""
import argparse
import importlib
import importlib.metadata
import json
import platform
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--root', type=Path, required=True)
parser.add_argument('--environment', required=True)
args = parser.parse_args()
specs = json.loads((args.root / 'environment/environments.json').read_text(encoding='utf-8'))
spec = next(s for s in specs if s['name'] == args.environment)
errors = []
if platform.python_version() != spec['python_version']:
    errors.append('Python version differs from export')
packages = json.loads((args.root / 'environment' / spec['name'] / 'package-list.json').read_text(encoding='utf-8'))
for package in packages:
    try:
        actual = importlib.metadata.version(package['name'])
        if actual != package['version']:
            errors.append(f"{package['name']}: expected {package['version']}, observed {actual}")
    except importlib.metadata.PackageNotFoundError:
        errors.append(f"{package['name']}: missing")
for module in spec['imports']:
    try:
        importlib.import_module(module)
    except Exception as exc:
        errors.append(f'{module}: {type(exc).__name__}: {exc}')
print(json.dumps({'environment': spec['name'], 'status': 'FAIL' if errors else 'PASS',
                  'python': platform.python_version(), 'packages_checked': len(packages),
                  'imports': spec['imports'], 'errors': errors}))
raise SystemExit(1 if errors else 0)
