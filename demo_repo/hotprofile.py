import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hotpath.profilelib import run
from data import make_records
from slowlib import pipeline

records = make_records(4000)
run(lambda: pipeline(records, k=10), top=20, repeat=2)
