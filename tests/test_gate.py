import copy

import pytest

from ttftkit.gate import evaluate


def report():
    return {'schema_version': 1, 'config': {'protocol': 'openai-sse-v1', 'model': 'tiny',
        'concurrency': 2, 'max_tokens': 32, 'requests': 8, 'prompt_sha256': 'abc',
        'profile': 'cpu-test'}, 'summary': {'requests': 8, 'successful_requests': 8,
        'error_rate': 0, 'ttft_ms': {'count': 8, 'p95': 100}, 'output_tokens_per_s': 20}}


def test_absolute_limit_rejects_violation_and_allows_boundary():
    assert evaluate(report(), {'limits': {'ttft_ms.p95': 100}})['exit_code'] == 0
    assert evaluate(report(), {'limits': {'ttft_ms.p95': 99}})['exit_code'] == 1


def test_regression_both_directions_and_exact_boundary():
    base, candidate = report(), report()
    candidate['summary']['ttft_ms']['p95'] = 110
    candidate['summary']['output_tokens_per_s'] = 18
    rules = {'regressions': {'ttft_ms.p95': 10, 'output_tokens_per_s': 10}}
    assert evaluate(candidate, rules, base)['exit_code'] == 0
    candidate['summary']['ttft_ms']['p95'] = 111
    assert evaluate(candidate, rules, base)['exit_code'] == 1
    candidate['summary']['ttft_ms']['p95'] = 100
    candidate['summary']['output_tokens_per_s'] = 17
    assert evaluate(candidate, rules, base)['exit_code'] == 1


@pytest.mark.parametrize('value', [None, float('nan'), float('inf'), '100', True])
def test_missing_or_invalid_metric_cannot_pass(value):
    candidate = report()
    candidate['summary']['ttft_ms']['p95'] = value
    assert evaluate(candidate, {'limits': {'ttft_ms.p95': 100}})['exit_code'] != 0


def test_zero_baseline_no_errors_may_become_errors():
    base, candidate = report(), report()
    # 计数必须自洽：100 个请求，candidate 失败 1 个 → error_rate = 0.01
    for rep in (base, candidate):
        rep['config']['requests'] = 100
        rep['summary']['requests'] = 100
        rep['summary']['successful_requests'] = 100
    rules = {'regressions': {'error_rate': 10}}
    assert evaluate(candidate, rules, base)['exit_code'] == 0
    candidate['summary']['successful_requests'] = 99
    candidate['summary']['error_rate'] = .01
    assert evaluate(candidate, rules, base)['exit_code'] == 1


@pytest.mark.parametrize('key', ['protocol', 'model', 'concurrency', 'max_tokens', 'requests',
                               'prompt_sha256', 'profile'])
def test_incompatible_or_missing_config_is_not_comparable(key):
    candidate = report()
    candidate['config'].pop(key)
    assert evaluate(candidate, {'regressions': {'ttft_ms.p95': 10}}, report())['exit_code'] == 2
    candidate = copy.deepcopy(report())
    candidate['config'][key] = 'changed'
    assert evaluate(candidate, {'regressions': {'ttft_ms.p95': 10}}, report())['exit_code'] == 2
    # 反向：两边配置一致时必须真的进入判定，而不是一律拒
    assert evaluate(report(), {'regressions': {'ttft_ms.p95': 10}}, report())['exit_code'] == 0


@pytest.mark.parametrize('rules', [{}, {'limits': {}}, {'limits': {'made_up': 10}},
    {'limits': {'ttft_ms.p95': -1}}, {'limits': {'ttft_ms.p95': float('nan')}},
    {'regressions': {'ttft_ms.p95': 10}}, {'limtis': {'ttft_ms.p95': 10}}])
def test_invalid_or_empty_policy_is_configuration_error(rules):
    assert evaluate(report(), rules)['exit_code'] == 2


def test_any_failed_requests_block_by_default():
    candidate = report()
    candidate['summary']['error_rate'] = .125
    candidate['summary']['successful_requests'] = 7
    candidate['summary']['ttft_ms']['count'] = 7  # 失败请求不贡献延迟样本
    assert evaluate(candidate, {'limits': {'ttft_ms.p95': 100}})['exit_code'] == 1
