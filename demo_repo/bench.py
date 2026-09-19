import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hotpath.benchlib import run
from data import make_records
from slowlib import pipeline

records = make_records(4000)
run(lambda: pipeline(records, k=10), warmup=2, trials=15, seed=0)
