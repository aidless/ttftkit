"""手算样例检验指标口径，避免以 chunk 数冒充 token 数。"""
import pytest

from ttftkit.metrics import distribution, summarize
from ttftkit.models import RequestRecord


def record(index=0, **kwargs):
    fields = {'index': index, 'ok': True, 'latency_ms': 100, 'ttft_ms': 40,
              'decode_ms': 60, 'content_chunks': 4, 'output_tokens': 4}
    fields.update(kwargs)
    return RequestRecord(**fields)


def test_tpot_uses_server_token_count_not_chunk_count():
    assert record(content_chunks=2).tpot_ms == 20
    assert record(output_tokens=None).tpot_ms is None
    assert record(output_tokens=1).tpot_ms is None
    assert record(content_chunks=1).tpot_ms is None
    assert record(ok=False).tpot_ms is None


def test_distribution_interpolates_percentiles():
    result = distribution([10, 20, 30, 40])
    assert result['p50'] == 25
    assert result['p95'] == pytest.approx(38.5)
    assert result['mean'] == 25
    assert distribution([]) is None


def test_summary_counts_failures_and_uses_wall_window():
    # 3 个请求、成功 2 个（4+6 token）、墙钟 4s：分母必须是墙钟，不能是请求数
    result = summarize([record(), record(1, output_tokens=6), record(2, ok=False)], 4)
    assert result['requests'] == 3
    assert result['successful_requests'] == 2
    assert result['error_rate'] == pytest.approx(1 / 3)
    assert result['requests_per_s'] == 0.5
    assert result['output_tokens_per_s'] == 2.5
    assert result['token_usage_coverage'] == 1


def test_missing_usage_is_unknown_not_zero_or_chunk_count():
    result = summarize([record(), record(1, output_tokens=None)], 1)
    assert result['output_tokens_per_s'] is None
    assert result['token_usage_coverage'] == 0.5
    assert result['tpot_ms']['count'] == 1
    assert summarize([record(ok=False)], 1)['ttft_ms'] is None


@pytest.mark.parametrize('seconds', [0, -1, float('nan'), float('inf')])
def test_summary_rejects_invalid_measurement_window(seconds):
    with pytest.raises(ValueError):
        summarize([record()], seconds)
