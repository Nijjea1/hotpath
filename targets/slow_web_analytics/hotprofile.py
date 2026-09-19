"""Locked profiler for the slow analytics demo target."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analytics import dashboard_payload
from data import make_events
from hotpath.profilelib import run

EVENTS = make_events()
run(lambda: dashboard_payload(EVENTS), top=20, repeat=1)
