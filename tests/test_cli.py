import asyncio
import json
import os
import subprocess
import sys

from ttftkit.report import markdown
from ttftkit.runner import run_benchmark


def cli(*args):
    return subprocess.run([sys.executable, '-m', 'ttftkit', *map(str, args)],
                          capture_output=True, text=True, check=False,
                          env={**os.environ, 'PYTHONPATH': 'src'})


def test_runner_warmup_excluded_and_config_saved(sse_base):
    result = asyncio.run(run_benchmark(endpoint=sse_base + '/normal/v1/chat/completions',
        model='tiny', prompt='hi', requests=4, concurrency=2, max_tokens=32,
        warmup=1, timeout_s=2, profile='http-fixture'))
    assert len(result['records']) == 4
    assert result['summary']['successful_requests'] == 4
    assert result['summary']['requests'] == 4
    assert result['summary']['output_tokens_per_s'] > 0
    assert result['config']['max_tokens'] == 32
    assert result['config']['warmup'] == 1
    assert result['config']['profile'] == 'http-fixture'
    assert 'hi' not in result['config'].values()
    assert 'endpoint' not in result['config']
    assert len(result['config']['prompt_sha256']) == 64
    text = markdown(result)
    assert 'TTFT' in text and 'TPOT' in text
    # 用字面量断言，不再「用 summarize 重算再和 summary 比」（自证，算错也两边同错）
    assert '4 / 4' in text
    ttft = result['summary']['ttft_ms']
    assert ttft['count'] == 4
    assert 0 < ttft['p50'] < 5000


def test_cli_end_to_end_run_report_gate(sse_base, tmp_path):
    out = tmp_path / 'run.json'
    run = cli('run', '--endpoint', sse_base + '/normal/v1/chat/completions',
              '--model', 'tiny', '--requests', 2, '--concurrency', 2, '--max-tokens', 32,
              '--warmup', 0, '--profile', 'fixture', '--output', out)
    assert run.returncode == 0, run.stderr
    result = json.loads(out.read_text())
    assert result['summary']['successful_requests'] == 2
    assert out.with_suffix('.md').exists()
    policy = tmp_path / 'policy.json'
    policy.write_text(json.dumps({'limits': {'ttft_ms.p95': 2000}}))
    assert cli('gate', out, '--policy', policy).returncode == 0
    policy.write_text(json.dumps({'limits': {'ttft_ms.p95': 0}}))
    assert cli('gate', out, '--policy', policy).returncode == 1
    policy.write_text('{}')
    assert cli('gate', out, '--policy', policy).returncode == 2
    assert cli('report', out).returncode == 0
    assert cli('gate', tmp_path / 'missing.json', '--policy', policy).returncode == 2


def test_failed_run_returns_nonzero_and_preserves_evidence(sse_base, tmp_path):
    out = tmp_path / 'failed.json'
    run = cli('run', '--endpoint', sse_base + '/http500/v1/chat/completions',
              '--model', 'tiny', '--requests', 2, '--output', out)
    assert run.returncode == 1
    text = out.read_text()
    assert 'boom' not in text  # 不将服务器响应体里的潜在凭据带入报告
    assert json.loads(text)['summary']['error_rate'] == 1


def test_report_partial_json_exits_2(tmp_path):
    # 修复前：残缺分布 KeyError 逃逸 → 退出码 1（会被误读为门禁失败）；应为 2
    bad = tmp_path / 'bad.json'
    bad.write_text(json.dumps({'summary': {'requests': 2, 'successful_requests': 2,
                                           'error_rate': 0, 'ttft_ms': {'p95': 100}}}))
    assert cli('report', bad).returncode == 2


def test_warmup_infra_failure_exits_1(sse_base, tmp_path):
    # 服务端 500 属「本轮不通过=1」，不是用法错误=2
    run = cli('run', '--endpoint', sse_base + '/http500/v1/chat/completions',
              '--model', 'tiny', '--requests', 1, '--warmup', 1,
              '--output', tmp_path / 'warmup.json')
    assert run.returncode == 1


def test_slo_enables_goodput(sse_base, tmp_path):
    out = tmp_path / 'slo.json'
    run = cli('run', '--endpoint', sse_base + '/normal/v1/chat/completions',
              '--model', 'tiny', '--requests', 2, '--slo-ttft', 10000, '--output', out)
    assert run.returncode == 0, run.stderr
    summary = json.loads(out.read_text())['summary']
    assert summary['goodput_per_s'] is not None
    assert summary['goodput_per_s'] > 0


def test_cli_invalid_parameters_have_usage_error(tmp_path):
    for flag in ('--requests', '--concurrency', '--max-tokens', '--timeout'):
        assert cli('run', '--endpoint', 'http://127.0.0.1:1/v1/chat/completions',
                   '--model', 'tiny', flag, 0, '--output', tmp_path / 'invalid.json').returncode == 2
    assert cli('run', '--endpoint', 'https://user:secret@example.com/v1?key=secret',
               '--model', 'tiny', '--output', tmp_path / 'invalid.json').returncode == 2


def test_version_flag_prints_the_installed_version():
    # 用户在报 bug 时第一件事就是抄版本号；没有 --version 就只能去翻文件
    proc = cli("--version")
    assert proc.returncode == 0, proc.stderr
    from ttftkit import __version__
    assert __version__ in (proc.stdout + proc.stderr)
