from app.sound import anomaly
from app.sound.features import N_MELS, embed, read_wav

from .helpers import engine_wav


def _emb(**kw):
    return embed(*read_wav(engine_wav(**kw)))


def test_embedding_shape():
    assert _emb().shape == (2 * N_MELS,)


def test_needs_baseline_first():
    result = anomaly.score(_emb(), [_emb(seed=1)])
    assert not result.is_anomaly
    assert result.baseline_clips == 1


def test_healthy_vs_faulty():
    baseline = [_emb(seed=s) for s in range(4)]
    healthy = anomaly.score(_emb(seed=10), baseline)
    faulty = anomaly.score(_emb(seed=11, knock=0.8), baseline)
    assert not healthy.is_anomaly
    assert faulty.is_anomaly
    assert faulty.score > healthy.score * 3
