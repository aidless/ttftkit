"""无效输入必须阻断，不能悄悄通过或抛未处理异常。"""
import asyncio

import httpx
import pytest
from conftest import SCENARIOS
from test_gate import report

from ttftkit.client import measure_request
from ttftkit.gate import evaluate


@pytest.mark.parametrize('payload', [[], None, {'usage': {'completion_tokens': -1}},
    {'usage': {'completion_tokens': '5'}}, {'usage': {'completion_tokens': True}},
    {'choices': [{'delta': {'content': 'x'}, 'finish_reason': 'content_filter'}]}])
def test_bad_stream_envelope_is_a_failed_record(sse_base, payload):
    import json
    SCENARIOS['invalid'] = [(0, b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'),
        (0, ('data: ' + json.dumps(payload) + '\n\n').encode()), (0, b'data: [DONE]\n\n')]
    async def go():
        async with httpx.AsyncClient() as client:
            return await measure_request(client, {}, path=sse_base + '/invalid', timeout_s=1)
    rec = asyncio.run(go())
    assert not rec.ok


@pytest.mark.parametrize('policy', [{'limits': []}, {'limits': 'wrong'},
    {'limits': {'ttft_ms.p95': 100}, 'regressions': []},
    {'limits': None}, {'limits': {'ttft_ms.p95': True}}])
def test_invalid_policy_shape_does_not_throw_or_pass(policy):
    assert evaluate(report(), policy)['exit_code'] == 2


@pytest.mark.parametrize('summary', [{}, {'requests': 0, 'successful_requests': 0},
    {'requests': '8', 'successful_requests': 8}, {'requests': 8, 'successful_requests': 9},
    {'requests': 8, 'successful_requests': 8, 'error_rate': .5}])
def test_invalid_or_empty_summary_cannot_pass(summary):
    candidate = report()
    candidate['summary'] = {**summary, 'ttft_ms': {'p95': 10}}
    assert evaluate(candidate, {'limits': {'ttft_ms.p95': 100}})['exit_code'] != 0


def test_partial_tpot_samples_cannot_pass_gate():
    candidate = report()
    candidate['summary']['tpot_ms'] = {'p95': 1, 'count': 1}
    assert evaluate(candidate, {'limits': {'tpot_ms.p95': 10}})['exit_code'] == 1
