# Trusted image only: build from the Hotpath root, never from a target repository.
FROM python:3.11-slim
WORKDIR /opt/hotpath
COPY hotpath/benchlib.py hotpath/profilelib.py ./hotpath/
RUN touch hotpath/__init__.py
ENV PYTHONPATH=/opt/hotpath
USER 65534:65534
WORKDIR /workspace
