"""Queued generation retains concurrency when workspace preferences change."""
import json
import pytest
from briefloop.store import Store
from briefloop.runtime import Worker
from briefloop.research_plan import freeze, mark_protocol
