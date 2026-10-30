"""Execute the actual workflow scripts under Bash with an owned argv recorder.

GitHub expression rendering is simulated; no workflow dispatch, secrets, provider
calls or external commands beyond the fake uv process are used.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/company-enrichment-eval.yml'


def step(name):
    lines = WORKFLOW.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == '- name: ' + name)
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('      - ')), len(lines))
    block = lines[start:end]
    run = next(i for i, line in enumerate(block) if line.lstrip().startswith('run:'))
    value = block[run].split('run:', 1)[1].strip()
    if value == '|':
        script = textwrap.dedent('\n'.join(block[run + 1:]))
    else:
        script = value
    # Resolve only the inputs actually declared as environment data by this step.
    bindings = re.findall(r'^\s+(\w+): \$\{\{ inputs\.(provider|limit) \}\}\s*$',
                          '\n'.join(block), flags=re.M)
    return script, bindings


def execute(tmp_path, name, provider='apollo', limit='10'):
    bash = shutil.which('bash')
    if not bash:
        pytest.skip('The GitHub Ubuntu workflow uses Bash')
    script, bindings = step(name)
    inputs = {'provider': provider, 'limit': limit}
    script = script.replace('${{ inputs.provider }}', provider)
    script = script.replace("${{ inputs.limit && format('--limit {0}', inputs.limit) || '' }}",
                            '--limit ' + limit if limit else '')
    assert '${{' not in script, 'Every shell-source expression must be rendered in the fixture'
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir(exist_ok=True)
    fake_uv = bin_dir / 'uv'
    fake_uv.write_text(f'#!{sys.executable}\nimport json,os,pathlib,sys\n'
                       'pathlib.Path(os.environ["CI_FIXTURE_ARGV"]).write_text(json.dumps(sys.argv[1:]))\n')
    fake_uv.chmod(0o755)
    safe_name = name.lower().replace(' ', '-')
    argv_path = tmp_path / (safe_name + '-argv.json')
    marker = tmp_path / (safe_name + '-unexpected-command')
    path = tmp_path / (safe_name + '.sh')
    path.write_text(script)
    env = {'PATH': str(bin_dir) + os.pathsep + os.defpath,
           'CI_FIXTURE_ARGV': str(argv_path), 'CI_FIXTURE_MARKER': str(marker)}
    env.update({key: inputs[value] for key, value in bindings})
    result = subprocess.run([bash, '-e', str(path)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(argv_path.read_text()), marker


@pytest.mark.parametrize('name', ['Run provider', 'Report'])
def test_provider_metacharacters_remain_one_literal_argument(tmp_path, name):
    provider = 'apollo$(printf injected > "$CI_FIXTURE_MARKER")'
    argv, marker = execute(tmp_path, name, provider=provider)
    assert not marker.exists(), 'An input must not execute a command before CLI validation'
    assert argv[argv.index('--label') + 1] == 'ci-' + provider
    if name == 'Run provider':
        assert argv[argv.index('--provider') + 1] == provider


def test_limit_metacharacters_are_data_not_shell_source(tmp_path):
    limit = '10$(printf injected > "$CI_FIXTURE_MARKER")'
    argv, marker = execute(tmp_path, 'Run provider', limit=limit)
    assert not marker.exists()
    assert argv[argv.index('--limit') + 1] == limit


def test_limit_cannot_add_a_second_cli_flag(tmp_path):
    argv, _ = execute(tmp_path, 'Run provider', limit='10 --skip-preflight')
    assert argv[argv.index('--limit') + 1] == '10 --skip-preflight'
    assert '--skip-preflight' not in argv


@pytest.mark.parametrize('limit', ['10', ''])
def test_normal_and_blank_smoke_limits_preserve_commands(tmp_path, limit):
    argv, marker = execute(tmp_path, 'Run provider', provider='ocean', limit=limit)
    expected = ['run', 'company-enrichment', '--provider', 'ocean']
    if limit:
        expected += ['--limit', limit]
    assert argv == expected + ['--label', 'ci-ocean']
    assert not marker.exists()
    argv, marker = execute(tmp_path, 'Report', provider='ocean', limit=limit)
    assert argv == ['run', 'company-enrichment-report', '--label', 'ci-ocean', '--out', 'results-ci.json']
    assert not marker.exists()
