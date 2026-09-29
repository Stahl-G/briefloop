import threading
import time
from briefloop.runtime import Worker
from briefloop.store import Store


def wait_for(predicate):
    end=time.monotonic()+5
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.02)
    assert predicate()


def test_deep_length_defaults_respect_explicit_user_length():
    from briefloop.models import Requirements
    req=Requirements(title='月报',objective='深度研究',research_tier='deep')
    assert (req.target_words,req.max_words)==(10000,12000)
    explicit=Requirements(title='专题',objective='精简',research_tier='deep',target_words=8000,max_words=9000)
    assert (explicit.target_words,explicit.max_words)==(8000,9000)
