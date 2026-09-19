"""Locked benchmark: reports seconds for one realistic analytics request batch."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analytics import dashboard_payload
from data import make_events
from hotpath.benchlib import run

EVENTS = make_events()
run(lambda: dashboard_payload(EVENTS), warmup=1, trials=7, seed=0)
